"""Celery tasks for account-lifecycle retention and final purge."""

import logging
from datetime import timedelta

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from core.shared.logging import log_security_event

logger = logging.getLogger("core.users")


@shared_task(name="core.users.tasks.cleanup_expired_username_reservations")
def cleanup_expired_username_reservations() -> int:
    """Discard expired claims; availability already ignores them immediately at expiry."""
    from core.users.models import UsernameReservation

    deleted, _ = UsernameReservation.objects.filter(expires_at__lte=timezone.now()).delete()
    return deleted


@shared_task(
    name="core.users.tasks.purge_due_account_deletions", soft_time_limit=240, time_limit=300
)
def purge_due_account_deletions(batch_size: int = 20) -> dict:
    """Purge up to ``batch_size`` due deletion requests, claiming each just before it runs.

    Claiming one at a time — rather than the whole batch up front — is what makes
    a run that hits the soft time limit safe to stop. A claimed request sits in
    PURGING until it completes or fails, and a stale PURGING claim is reclaimed
    with a retry counted against it. Had the batch been claimed up front, every
    request not yet started would be stranded and penalised for a timeout it had
    nothing to do with. Only the request in flight is ever left behind.
    """
    limit = max(1, min(batch_size, 100))
    attempted: list[str] = []
    completed = 0
    failed = 0

    while len(attempted) < limit:
        # Excluding this run's attempts keeps a failed request from being
        # reclaimed straight back in the same run.
        claimed = _claim_due_requests(1, exclude_ids=attempted)
        if not claimed:
            break
        request_id = claimed[0]
        attempted.append(request_id)
        try:
            _purge_account_deletion(request_id)
            completed += 1
        except SoftTimeLimitExceeded:
            # Out of time — not this request's failure. It stays PURGING and
            # is reclaimed like any interrupted purge (purges are idempotent).
            raise
        except Exception as exc:  # noqa: BLE001
            failed += 1
            _record_purge_failure(request_id, exc)

    return {"claimed": len(attempted), "completed": completed, "failed": failed}


def _claim_due_requests(batch_size: int, exclude_ids=()) -> list[str]:
    from core.users.models import AccountDeletionRequest, AccountDeletionStatus

    now = timezone.now()
    stale_purge_before = now - timedelta(hours=1)
    retry_limit = settings.ACCOUNT_DELETION_PURGE_MAX_RETRIES

    with transaction.atomic():
        requests = list(
            AccountDeletionRequest.objects.select_for_update(skip_locked=True)
            .filter(
                Q(status=AccountDeletionStatus.PENDING, scheduled_for__lte=now)
                | Q(
                    status=AccountDeletionStatus.FAILED,
                    scheduled_for__lte=now,
                    retry_count__lt=retry_limit,
                )
                | Q(
                    status=AccountDeletionStatus.PURGING,
                    updated_at__lte=stale_purge_before,
                    retry_count__lt=retry_limit,
                )
            )
            .exclude(id__in=list(exclude_ids))
            .order_by("scheduled_for")[:batch_size]
        )
        for deletion_request in requests:
            update_fields = ["status", "failure_code", "updated_at"]
            # A row already in PURGING is a stale/crashed re-claim: the worker died
            # mid-purge before _record_purge_failure could run. Count it toward the
            # retry cap so a purge that keeps crashing the worker cannot loop forever
            # (the retry_count__lt filter above then excludes it once exhausted).
            if deletion_request.status == AccountDeletionStatus.PURGING:
                if deletion_request.retry_count + 1 >= retry_limit:
                    logger.error(
                        "account_deletion_purge_exhausted",
                        extra={
                            "deletion_request_id": str(deletion_request.id),
                            "retry_count": deletion_request.retry_count + 1,
                            "reason": "stale_purging",
                        },
                    )
                deletion_request.retry_count = F("retry_count") + 1
                update_fields.append("retry_count")
            deletion_request.status = AccountDeletionStatus.PURGING
            deletion_request.failure_code = ""
            deletion_request.save(update_fields=update_fields)
        return [str(deletion_request.id) for deletion_request in requests]


def _purge_account_deletion(request_id: str) -> None:
    from core.users.account_lifecycle import (
        anonymize_user_for_permanent_delete,
        delete_user_gcs_objects,
        remove_or_hide_user_data,
    )
    from core.users.models import AccountDeletionRequest, AccountDeletionStatus, User

    deletion_request = AccountDeletionRequest.objects.select_related("user").get(id=request_id)
    if deletion_request.status != AccountDeletionStatus.PURGING:
        return

    log_security_event(
        "auth.account_deletion_purge_started",
        user_id=str(deletion_request.user_id),
        metadata={"deletion_request_id": request_id},
    )
    deleted_objects = delete_user_gcs_objects(deletion_request.user)
    now = timezone.now()

    with transaction.atomic():
        deletion_request = AccountDeletionRequest.objects.select_for_update().get(id=request_id)
        if deletion_request.status != AccountDeletionStatus.PURGING:
            return
        user = User.all_objects.select_for_update().get(id=deletion_request.user_id)
        remove_or_hide_user_data(user, now)
        # Moderation history (warnings/suspensions) is intentionally retained for
        # trust & safety audit — the row already points at the anonymized user.
        anonymize_user_for_permanent_delete(user, now)

        deletion_request.status = AccountDeletionStatus.COMPLETED
        deletion_request.completed_at = now
        deletion_request.failure_code = ""
        deletion_request.save(
            update_fields=["status", "completed_at", "failure_code", "updated_at"]
        )

    log_security_event(
        "auth.account_deletion_completed",
        user_id=str(deletion_request.user_id),
        metadata={"gcs_objects_deleted": deleted_objects},
    )
    logger.info(
        "account_deletion_purge_completed",
        extra={"deletion_request_id": request_id, "gcs_objects_deleted": deleted_objects},
    )


def _record_purge_failure(request_id: str, exc: Exception) -> None:
    from core.users.models import AccountDeletionRequest, AccountDeletionStatus

    failure_code = type(exc).__name__.upper()[:80]
    user_id = (
        AccountDeletionRequest.objects.filter(id=request_id)
        .values_list("user_id", flat=True)
        .first()
    )
    AccountDeletionRequest.objects.filter(id=request_id).update(
        status=AccountDeletionStatus.FAILED,
        retry_count=F("retry_count") + 1,
        failure_code=failure_code,
        updated_at=timezone.now(),
    )
    retry_count = (
        AccountDeletionRequest.objects.filter(id=request_id)
        .values_list("retry_count", flat=True)
        .first()
    )
    log_security_event(
        "auth.account_deletion_purge_failed",
        user_id=str(user_id) if user_id else None,
        metadata={
            "deletion_request_id": request_id,
            "failure_code": failure_code,
        },
    )
    logger.error(
        "account_deletion_purge_failed",
        extra={"deletion_request_id": request_id, "failure_code": failure_code},
        exc_info=True,
    )
    # Dead-letter alert: a request that has exhausted its retries is never
    # reclaimed again, so surface it loudly (Sentry) — the user's PII would
    # otherwise sit un-purged indefinitely with no signal.
    if retry_count is not None and retry_count >= settings.ACCOUNT_DELETION_PURGE_MAX_RETRIES:
        log_security_event(
            "auth.account_deletion_purge_exhausted",
            user_id=str(user_id) if user_id else None,
            metadata={
                "deletion_request_id": request_id,
                "retry_count": retry_count,
                "failure_code": failure_code,
            },
        )
        logger.error(
            "account_deletion_purge_exhausted",
            extra={
                "deletion_request_id": request_id,
                "retry_count": retry_count,
                "failure_code": failure_code,
            },
        )

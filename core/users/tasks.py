"""Celery tasks for account-lifecycle retention and final purge."""

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from core.shared.logging import log_security_event

logger = logging.getLogger("core.users")


@shared_task(name="core.users.tasks.purge_due_account_deletions")
def purge_due_account_deletions(batch_size: int = 20) -> dict:
    """Claim and purge due deletion requests in a small idempotent batch."""
    request_ids = _claim_due_requests(max(1, min(batch_size, 100)))
    completed = 0
    failed = 0

    for request_id in request_ids:
        try:
            _purge_account_deletion(request_id)
            completed += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            _record_purge_failure(request_id, exc)

    return {"claimed": len(request_ids), "completed": completed, "failed": failed}


def _claim_due_requests(batch_size: int) -> list[str]:
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
                )
            )
            .order_by("scheduled_for")[:batch_size]
        )
        for deletion_request in requests:
            deletion_request.status = AccountDeletionStatus.PURGING
            deletion_request.failure_code = ""
            deletion_request.save(update_fields=["status", "failure_code", "updated_at"])
        return [str(deletion_request.id) for deletion_request in requests]


def _purge_account_deletion(request_id: str) -> None:
    from core.admin_dashboard.models import ModerationAction
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
        ModerationAction.objects.filter(user=user).delete()
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

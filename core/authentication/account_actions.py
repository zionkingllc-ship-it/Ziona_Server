"""Account lifecycle actions — deactivate, delete, reactivate, cancel deletion.

Split from core/authentication/services.py; attached to AuthService as
staticmethods so its public surface is unchanged (no behavior change).
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from core.authentication.account_status import (
    ensure_account_can_authenticate,
)
from core.authentication.activity import record_successful_auth
from core.authentication.otp_service import OTPService
from core.authentication.tokens import TokenError, TokenService
from core.authentication.validators import (
    AuthenticationError,
)
from core.shared.logging import log_security_event, mask_email
from core.users.models import User

logger = logging.getLogger("core.authentication")


def _issue_normal_token_pair(user: User) -> dict[str, str]:
    """Issue a normal access/refresh pair after lifecycle recovery."""
    access_token = TokenService.generate_access_token(str(user.id), user.role)
    try:
        refresh_token, _ = TokenService.generate_refresh_token(str(user.id))
    except TokenError as exc:
        raise AuthenticationError(
            "Authentication service is temporarily unavailable. Please try again.",
            code="AUTH_SERVICE_UNAVAILABLE",
        ) from exc
    return {"access_token": access_token, "refresh_token": refresh_token}


def _deletion_request_result(deletion_request) -> dict:
    return {
        "status": "PENDING_DELETION",
        "requested_at": deletion_request.requested_at.isoformat(),
        "scheduled_for": deletion_request.scheduled_for.isoformat(),
        "grace_period_days": settings.ACCOUNT_DELETION_GRACE_DAYS,
        "can_cancel": deletion_request.status == "pending",
    }


def _verify_account_action_reauthentication(
    user: User,
    *,
    password: str | None = None,
    otp: str | None = None,
    otp_purpose: str,
) -> None:
    """Require a fresh credential before sensitive account actions."""
    password = (password or "").strip()
    otp = (otp or "").strip()

    if password:
        if not user.has_usable_password():
            raise AuthenticationError(
                "Password authentication is not available for this account. Use OTP instead.",
                code="PASSWORD_AUTH_UNAVAILABLE",
            )
        if not user.check_password(password):
            raise AuthenticationError(
                "Invalid password.",
                code="INVALID_CREDENTIALS",
            )
        return

    if otp:
        OTPService.verify_account_action_otp(
            email=user.email,
            user_id=str(user.id),
            code=otp,
            purpose=otp_purpose,
        )
        return

    raise AuthenticationError(
        "Password or OTP is required for this account action.",
        code="REAUTHENTICATION_REQUIRED",
    )


@transaction.atomic
def deactivate_account(
    user_id: str,
    *,
    password: str | None = None,
    otp: str | None = None,
    ip_address: str | None = None,
) -> bool:
    """Deactivate a user account without deleting user data."""
    user = User.all_objects.select_for_update().filter(id=user_id, deleted_at__isnull=True).first()
    if not user:
        raise AuthenticationError("User not found", "USER_NOT_FOUND")

    ensure_account_can_authenticate(user)
    _verify_account_action_reauthentication(
        user,
        password=password,
        otp=otp,
        otp_purpose="account_deactivation",
    )

    from core.users.account_lifecycle import revoke_user_sessions
    from core.users.models import UserLifecycleState

    revoke_user_sessions(user.id, delete_device_tokens=False)
    user.lifecycle_state = UserLifecycleState.DEACTIVATED
    user.is_active = False
    user.save(update_fields=["lifecycle_state", "is_active", "updated_at"])
    cache.delete(f"user_me_data_{user.id}")

    logger.info("Account deactivated: user_id=%s", user.id)
    log_security_event(
        "auth.account_deactivated",
        user_id=str(user.id),
        ip_address=ip_address,
        metadata={"email": mask_email(user.email)},
    )
    return True


@transaction.atomic
def delete_account(
    user_id: str,
    *,
    password: str | None = None,
    otp: str | None = None,
    acknowledge_permanent_deletion: bool = False,
    ip_address: str | None = None,
) -> dict:
    """Schedule reversible deletion and hide the account for 30 days."""
    if not acknowledge_permanent_deletion:
        raise AuthenticationError(
            "You must acknowledge the permanent deletion scheduled after the recovery period.",
            code="DELETION_ACKNOWLEDGEMENT_REQUIRED",
            details={
                "field": "acknowledgePermanentDeletion",
                "expected": True,
                "received": acknowledge_permanent_deletion,
                "acceptedFields": [
                    "acknowledgePermanentDeletion",
                    "acknowledge_permanent_deletion",
                    "permanentDeletionAcknowledged",
                ],
            },
        )

    user = User.all_objects.select_for_update().filter(id=user_id, deleted_at__isnull=True).first()
    if not user:
        logger.error("Delete account failed: user not found id=%s", user_id)
        raise AuthenticationError("User not found", "USER_NOT_FOUND")

    from core.users.models import (
        AccountDeletionRequest,
        AccountDeletionStatus,
        UserLifecycleState,
    )

    if user.lifecycle_state == UserLifecycleState.PENDING_DELETION:
        existing = AccountDeletionRequest.objects.filter(user=user).first()
        if existing and existing.status in {
            AccountDeletionStatus.PENDING,
            AccountDeletionStatus.PURGING,
            AccountDeletionStatus.FAILED,
        }:
            return _deletion_request_result(existing)

    ensure_account_can_authenticate(user)
    _verify_account_action_reauthentication(
        user,
        password=password,
        otp=otp,
        otp_purpose="account_deletion",
    )

    from core.users.account_lifecycle import revoke_user_sessions

    now = timezone.now()
    scheduled_for = now + timedelta(days=settings.ACCOUNT_DELETION_GRACE_DAYS)
    revoke_user_sessions(user.id, delete_device_tokens=True)
    user.lifecycle_state = UserLifecycleState.PENDING_DELETION
    user.is_active = False
    user.save(update_fields=["lifecycle_state", "is_active", "updated_at"])

    deletion_request, _ = AccountDeletionRequest.objects.update_or_create(
        user=user,
        defaults={
            "status": AccountDeletionStatus.PENDING,
            "requested_at": now,
            "scheduled_for": scheduled_for,
            "cancelled_at": None,
            "completed_at": None,
            "retry_count": 0,
            "failure_code": "",
        },
    )
    cache.delete(f"user_me_data_{user.id}")

    logger.info("Account deletion scheduled: user_id=%s", user_id)
    log_security_event(
        "auth.account_deletion_requested",
        user_id=str(user_id),
        ip_address=ip_address,
        metadata={"scheduled_for": scheduled_for.isoformat()},
    )
    return _deletion_request_result(deletion_request)


@transaction.atomic
def reactivate_account(
    recovery_token: str,
    *,
    confirm_reactivation: bool,
    ip_address: str | None = None,
) -> dict:
    """Reactivate a voluntarily deactivated account after explicit confirmation."""
    if not confirm_reactivation:
        raise AuthenticationError(
            "You must confirm account reactivation.",
            code="REACTIVATION_CONFIRMATION_REQUIRED",
        )
    try:
        payload = TokenService.validate_account_recovery_token(
            recovery_token,
            expected_reason="DEACTIVATED",
        )
    except TokenError as exc:
        raise AuthenticationError(str(exc), code="INVALID_RECOVERY_TOKEN") from exc

    from core.users.models import UserLifecycleState

    user = User.all_objects.select_for_update().filter(id=payload["user_id"]).first()
    if not user or user.lifecycle_state != UserLifecycleState.DEACTIVATED:
        raise AuthenticationError("Account cannot be reactivated.", "INVALID_RECOVERY_STATE")

    user.lifecycle_state = UserLifecycleState.ACTIVE
    user.is_active = True
    user.deleted_at = None
    user.save(update_fields=["lifecycle_state", "is_active", "deleted_at", "updated_at"])
    result = _issue_normal_token_pair(user)
    record_successful_auth(user, ip_address)
    log_security_event("auth.account_reactivated", user_id=str(user.id), ip_address=ip_address)
    return {"user": user, **result}


@transaction.atomic
def cancel_account_deletion(
    recovery_token: str,
    *,
    confirm_cancellation: bool,
    ip_address: str | None = None,
) -> dict:
    """Cancel a pending deletion during its 30-day recovery window."""
    if not confirm_cancellation:
        raise AuthenticationError(
            "You must confirm deletion cancellation.",
            code="DELETION_CANCELLATION_CONFIRMATION_REQUIRED",
        )
    try:
        payload = TokenService.validate_account_recovery_token(
            recovery_token,
            expected_reason="PENDING_DELETION",
        )
    except TokenError as exc:
        raise AuthenticationError(str(exc), code="INVALID_RECOVERY_TOKEN") from exc

    from core.users.models import (
        AccountDeletionRequest,
        AccountDeletionStatus,
        UserLifecycleState,
    )

    user = User.all_objects.select_for_update().filter(id=payload["user_id"]).first()
    deletion_request = (
        AccountDeletionRequest.objects.select_for_update().filter(user=user).first()
        if user
        else None
    )
    if (
        not user
        or not deletion_request
        or user.lifecycle_state != UserLifecycleState.PENDING_DELETION
        or deletion_request.status != AccountDeletionStatus.PENDING
    ):
        raise AuthenticationError("Deletion can no longer be cancelled.", "INVALID_RECOVERY_STATE")
    if deletion_request.scheduled_for <= timezone.now():
        raise AuthenticationError(
            "The account deletion recovery window has ended.",
            "RECOVERY_WINDOW_EXPIRED",
        )

    deletion_request.status = AccountDeletionStatus.CANCELLED
    deletion_request.cancelled_at = timezone.now()
    deletion_request.failure_code = ""
    deletion_request.save(update_fields=["status", "cancelled_at", "failure_code", "updated_at"])
    user.lifecycle_state = UserLifecycleState.ACTIVE
    user.is_active = True
    user.deleted_at = None
    user.save(update_fields=["lifecycle_state", "is_active", "deleted_at", "updated_at"])
    result = _issue_normal_token_pair(user)
    record_successful_auth(user, ip_address)
    log_security_event(
        "auth.account_deletion_cancelled",
        user_id=str(user.id),
        ip_address=ip_address,
    )
    return {"user": user, **result}

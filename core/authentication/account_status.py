"""Account status checks shared by token-issuing authentication flows."""

from core.authentication.validators import AuthenticationError
from core.users.models import UserLifecycleState, UserStatus

SUSPENDED_ACCOUNT_MESSAGE = (
    "This account has been suspended. If you believe this was a mistake, contact support@ziona.app."
)
DELETED_ACCOUNT_MESSAGE = "This account does not exist."


def ensure_account_can_authenticate(user) -> None:
    """Reject deleted, inactive, or suspended users before issuing tokens."""
    lifecycle_state = getattr(user, "lifecycle_state", UserLifecycleState.ACTIVE)
    if (
        getattr(user, "deleted_at", None) is not None
        or lifecycle_state == UserLifecycleState.DELETED
    ):
        raise AuthenticationError(DELETED_ACCOUNT_MESSAGE, code="ACCOUNT_NOT_FOUND")

    if getattr(user, "status", None) == UserStatus.SUSPENDED:
        raise AuthenticationError(SUSPENDED_ACCOUNT_MESSAGE, code="ACCOUNT_SUSPENDED")

    if lifecycle_state == UserLifecycleState.PENDING_DELETION:
        raise AuthenticationError(
            "This account is pending deletion.",
            code="ACCOUNT_PENDING_DELETION",
        )

    if lifecycle_state == UserLifecycleState.DEACTIVATED or not getattr(user, "is_active", True):
        raise AuthenticationError("This account has been deactivated", code="ACCOUNT_DEACTIVATED")


def build_account_recovery_result(user) -> dict | None:
    """Return a recovery-only login result for recoverable inactive accounts."""
    lifecycle_state = getattr(user, "lifecycle_state", UserLifecycleState.ACTIVE)
    if lifecycle_state not in {
        UserLifecycleState.DEACTIVATED,
        UserLifecycleState.PENDING_DELETION,
    }:
        return None

    from core.authentication.tokens import TokenService

    reason = (
        "DEACTIVATED" if lifecycle_state == UserLifecycleState.DEACTIVATED else "PENDING_DELETION"
    )
    result = {
        "user": user,
        "requires_account_recovery": True,
        "recovery_reason": reason,
        "recovery_token": TokenService.generate_account_recovery_token(
            str(user.id),
            reason,
        ),
    }
    if reason == "PENDING_DELETION":
        deletion_request = getattr(user, "account_deletion_request", None)
        result["deletion_scheduled_for"] = (
            deletion_request.scheduled_for.isoformat()
            if deletion_request and deletion_request.scheduled_for
            else None
        )
    return result

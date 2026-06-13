"""Account status checks shared by token-issuing authentication flows."""

from core.authentication.validators import AuthenticationError
from core.users.models import UserStatus

SUSPENDED_ACCOUNT_MESSAGE = (
    "This account has been suspended. If you believe this was a mistake, contact support@ziona.app."
)
DELETED_ACCOUNT_MESSAGE = "This account does not exist."


def ensure_account_can_authenticate(user) -> None:
    """Reject deleted, inactive, or suspended users before issuing tokens."""
    if getattr(user, "deleted_at", None) is not None:
        raise AuthenticationError(DELETED_ACCOUNT_MESSAGE, code="ACCOUNT_NOT_FOUND")

    if getattr(user, "status", None) == UserStatus.SUSPENDED:
        raise AuthenticationError(SUSPENDED_ACCOUNT_MESSAGE, code="ACCOUNT_SUSPENDED")

    if not getattr(user, "is_active", True):
        raise AuthenticationError("This account has been deactivated", code="ACCOUNT_DEACTIVATED")

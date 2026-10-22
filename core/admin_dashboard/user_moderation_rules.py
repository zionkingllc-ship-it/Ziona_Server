"""Moderation state-machine rules + admin user serialization.

Split from core/admin_dashboard/user_services.py (no behavior change).
"""

from core.shared.exceptions import AdminError, ErrorCode


def _user_to_dict(user) -> dict:
    """Convert User model to admin-facing dict."""
    posts_count = getattr(user, "posts_count", 0)
    submitted_reports = getattr(user, "submitted_report_count", 0)
    received_reports = getattr(user, "received_report_count", 0)

    deletion_request = getattr(user, "account_deletion_request", None)
    return {
        "id": str(user.id),
        "username": user.username,
        "email": user.email,
        "full_name": user.full_name,
        "avatar_url": user.avatar_url or "",
        "bio": user.bio or "",
        "status": user.status,
        "role": user.role,
        "is_email_verified": user.is_email_verified,
        "is_active": user.is_active,
        "deleted_at": user.deleted_at.isoformat() if user.deleted_at else None,
        "account_state": _account_state(user),
        "lifecycle_state": user.lifecycle_state,
        "deletion_status": deletion_request.status if deletion_request else None,
        "deletion_requested_at": (
            deletion_request.requested_at.isoformat() if deletion_request else None
        ),
        "deletion_scheduled_for": (
            deletion_request.scheduled_for.isoformat() if deletion_request else None
        ),
        "posts_count": posts_count,
        "submitted_reports": submitted_reports,
        "received_reports": received_reports,
        "warned_at": user.warned_at.isoformat() if user.warned_at else None,
        "suspended_at": user.suspended_at.isoformat() if user.suspended_at else None,
        "suspension_reason": user.suspension_reason,
        "created_at": user.created_at.isoformat() if user.created_at else "",
        "last_login": user.last_login.isoformat() if user.last_login else None,
        "available_actions": _available_actions(user),
    }


def _validate_reason(reason: str) -> str:
    cleaned = (reason or "").strip()
    if not cleaned:
        raise AdminError(
            message="A moderation reason is required.",
            code=ErrorCode.VALIDATION_ERROR,
        )
    return cleaned


def _available_actions(user) -> list[str]:
    from core.users.models import UserLifecycleState, UserStatus

    if getattr(user, "is_admin", False):
        return []
    if getattr(user, "deleted_at", None):
        return _deleted_account_actions(user)
    if user.lifecycle_state == UserLifecycleState.PENDING_DELETION:
        return []
    if not getattr(user, "is_active", True):
        return ["reactivate", "delete"]
    if user.status == UserStatus.SUSPENDED:
        return ["reactivate", "delete"]
    if user.status == UserStatus.WARNED:
        return ["suspend", "delete"]
    return ["warn", "suspend", "delete"]


def _is_admin_soft_deleted(user) -> bool:
    """Deleted by an admin's plain Delete — the account and its content are intact.

    Excludes anonymized tombstones (permanent deletion, or a completed
    user-requested deletion) and accounts in a user-requested deletion grace
    period or deactivation, which the user reverses themselves.
    """
    from core.users.account_lifecycle import is_anonymized
    from core.users.models import UserLifecycleState

    return (
        getattr(user, "deleted_at", None) is not None
        and user.lifecycle_state == UserLifecycleState.DELETED
        and not is_anonymized(user)
    )


def _deleted_account_actions(user) -> list[str]:
    """A soft-deleted account can be brought back, or finished off."""
    if _is_admin_soft_deleted(user):
        return ["restore", "permanently_delete"]
    return []


def _account_state(user) -> str:
    from core.users.models import UserLifecycleState, UserStatus

    if getattr(user, "deleted_at", None):
        return "deleted"
    if user.lifecycle_state != UserLifecycleState.ACTIVE:
        return user.lifecycle_state
    if not getattr(user, "is_active", True):
        return "inactive"
    if user.status == UserStatus.SUSPENDED:
        return "suspended"
    if user.status == UserStatus.WARNED:
        return "warned"
    return "active"


def _ensure_action_available(user, action: str) -> None:
    if action not in _available_actions(user):
        raise AdminError(
            message=f"Action '{action}' is not available for this user's current status.",
            code=ErrorCode.VALIDATION_ERROR,
        )

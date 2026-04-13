"""
User Management service — admin operations for user moderation.

All mutations are atomic with audit logging. Token revocation on suspend.
"""

import logging
from datetime import datetime, timezone

from django.db import transaction
from django.db.models import Count, Q

from core.admin_dashboard.permissions import log_admin_action
from core.shared.exceptions import AdminError, ErrorCode

logger = logging.getLogger("core.admin_dashboard")


class UserManagementService:
    """Service for admin user listing, search, and moderation actions."""

    @staticmethod
    def list_users(
        search: str = "",
        status_filter: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """List users with search, filter, and pagination.

        Avoids N+1 by annotating post/comment counts in a single query.

        Args:
            search: Search by username, full_name, or email.
            status_filter: Filter by UserStatus value.
            page: 1-indexed page number.
            page_size: Items per page (max 50).

        Returns:
            Dict with users list, total_count, page, page_size, total_pages,
            and summary counts (total, active, warned, suspended).
        """
        from core.users.models import User, UserStatus

        page_size = min(page_size, 50)
        offset = (page - 1) * page_size

        qs = (
            User.objects.filter(deleted_at__isnull=True)
            .annotate(
                posts_count=Count("posts", filter=Q(posts__deleted_at__isnull=True)),
            )
            .select_related()
        )

        if search:
            qs = qs.filter(
                Q(username__icontains=search)
                | Q(full_name__icontains=search)
                | Q(email__icontains=search)
            )

        if status_filter:
            qs = qs.filter(status=status_filter)

        total_count = qs.count()
        users = list(qs.order_by("-created_at")[offset : offset + page_size])

        # Summary counts (single query with conditional aggregation)
        summary = User.objects.filter(deleted_at__isnull=True).aggregate(
            total=Count("id"),
            active=Count("id", filter=Q(status=UserStatus.ACTIVE)),
            warned=Count("id", filter=Q(status=UserStatus.WARNED)),
            suspended=Count("id", filter=Q(status=UserStatus.SUSPENDED)),
        )

        return {
            "users": [_user_to_dict(u) for u in users],
            "total_count": total_count,
            "page": page,
            "page_size": page_size,
            "total_pages": max(1, (total_count + page_size - 1) // page_size),
            "summary": summary,
        }

    @staticmethod
    @transaction.atomic
    def warn_user(user_id: str, reason: str, admin_user, ip_address: str = "") -> dict:
        """Warn a user. Sets status to WARNED and sends notification.

        Idempotent: if user is already warned, raises USER_ALREADY_WARNED.

        Args:
            user_id: UUID of the user to warn.
            reason: Human-readable reason for the warning.
            admin_user: The admin performing the action.
            ip_address: Admin's IP address for audit.

        Returns:
            Dict with success status and user data.

        Raises:
            AdminError: If user not found or already warned.
        """
        from core.admin_dashboard.models import ModerationAction, ModerationActionType
        from core.users.models import User, UserStatus

        user = User.objects.select_for_update().filter(id=user_id, deleted_at__isnull=True).first()

        if not user:
            raise AdminError(
                message="User not found.",
                code=ErrorCode.USER_NOT_FOUND,
            )

        if user.status == UserStatus.WARNED:
            raise AdminError(
                message="User is already warned.",
                code=ErrorCode.USER_ALREADY_WARNED,
            )

        # Capture before state
        before_status = user.status

        user.status = UserStatus.WARNED
        user.warned_at = datetime.now(timezone.utc)
        user.save(update_fields=["status", "warned_at", "updated_at"])

        # Create moderation action record
        ModerationAction.objects.create(
            user=user,
            action_type=ModerationActionType.WARNED,
            reason=reason,
            admin_user=admin_user,
            metadata={"before_status": before_status},
        )

        # Audit log
        log_admin_action(
            admin_user=admin_user,
            action="USER_WARNED",
            target_type="User",
            target_id=str(user.id),
            details={"reason": reason, "before_status": before_status},
            ip_address=ip_address,
        )

        # Send notification to user
        _notify_user_moderation(user, "warned", reason)

        logger.info(
            "user_warned",
            extra={"user_id": user_id, "admin_id": str(admin_user.id), "reason": reason},
        )

        return {"success": True, "user": _user_to_dict(user)}

    @staticmethod
    @transaction.atomic
    def suspend_user(user_id: str, reason: str, admin_user, ip_address: str = "") -> dict:
        """Suspend a user. Revokes all tokens, blocking immediate access.

        Idempotent: if already suspended, raises USER_ALREADY_SUSPENDED.
        """
        from core.admin_dashboard.models import ModerationAction, ModerationActionType
        from core.authentication.tokens import TokenService
        from core.users.models import User, UserStatus

        user = User.objects.select_for_update().filter(id=user_id, deleted_at__isnull=True).first()

        if not user:
            raise AdminError(
                message="User not found.",
                code=ErrorCode.USER_NOT_FOUND,
            )

        if user.status == UserStatus.SUSPENDED:
            raise AdminError(
                message="User is already suspended.",
                code=ErrorCode.USER_ALREADY_SUSPENDED,
            )

        before_status = user.status

        user.status = UserStatus.SUSPENDED
        user.suspended_at = datetime.now(timezone.utc)
        user.suspension_reason = reason
        user.save(update_fields=["status", "suspended_at", "suspension_reason", "updated_at"])

        # Revoke ALL user tokens (forces logout from all devices)
        TokenService.revoke_all_user_tokens(str(user.id))

        ModerationAction.objects.create(
            user=user,
            action_type=ModerationActionType.SUSPENDED,
            reason=reason,
            admin_user=admin_user,
            metadata={"before_status": before_status},
        )

        log_admin_action(
            admin_user=admin_user,
            action="USER_SUSPENDED",
            target_type="User",
            target_id=str(user.id),
            details={"reason": reason, "before_status": before_status},
            ip_address=ip_address,
        )

        _notify_user_moderation(user, "suspended", reason)

        logger.info(
            "user_suspended",
            extra={"user_id": user_id, "admin_id": str(admin_user.id)},
        )

        return {"success": True, "user": _user_to_dict(user)}

    @staticmethod
    @transaction.atomic
    def delete_user(user_id: str, admin_user, ip_address: str = "") -> dict:
        """Soft-delete a user account.

        Args:
            user_id: UUID of the user to delete.
            admin_user: The admin performing the action.
            ip_address: Admin's IP for audit.

        Raises:
            AdminError: If user not found or admin tries to delete self.
        """
        from core.admin_dashboard.models import ModerationAction, ModerationActionType
        from core.authentication.tokens import TokenService
        from core.users.models import User

        if str(admin_user.id) == user_id:
            raise AdminError(
                message="Cannot delete your own account.",
                code=ErrorCode.USER_CANNOT_DELETE_SELF,
            )

        user = User.objects.select_for_update().filter(id=user_id, deleted_at__isnull=True).first()

        if not user:
            raise AdminError(
                message="User not found.",
                code=ErrorCode.USER_NOT_FOUND,
            )

        # Snapshot before delete
        user_snapshot = {
            "username": user.username,
            "email": user.email,
            "full_name": user.full_name,
            "status": user.status,
            "created_at": user.created_at.isoformat() if user.created_at else None,
        }

        # Soft delete
        user.deleted_at = datetime.now(timezone.utc)
        user.is_active = False
        user.save(update_fields=["deleted_at", "is_active", "updated_at"])

        # Revoke all tokens
        TokenService.revoke_all_user_tokens(str(user.id))

        ModerationAction.objects.create(
            user=user,
            action_type=ModerationActionType.DELETED,
            reason="Admin deleted account",
            admin_user=admin_user,
            metadata={"user_snapshot": user_snapshot},
        )

        log_admin_action(
            admin_user=admin_user,
            action="USER_DELETED",
            target_type="User",
            target_id=str(user.id),
            details={"user_snapshot": user_snapshot},
            ip_address=ip_address,
        )

        logger.info(
            "user_deleted",
            extra={"user_id": user_id, "admin_id": str(admin_user.id)},
        )

        return {"success": True}

    @staticmethod
    @transaction.atomic
    def reactivate_user(user_id: str, admin_user, ip_address: str = "") -> dict:
        """Reactivate a warned or suspended user. Resets status to ACTIVE."""
        from core.admin_dashboard.models import ModerationAction, ModerationActionType
        from core.users.models import User, UserStatus

        user = User.objects.select_for_update().filter(id=user_id, deleted_at__isnull=True).first()

        if not user:
            raise AdminError(
                message="User not found.",
                code=ErrorCode.USER_NOT_FOUND,
            )

        before_status = user.status

        user.status = UserStatus.ACTIVE
        user.warned_at = None
        user.suspended_at = None
        user.suspension_reason = ""
        user.save(
            update_fields=[
                "status",
                "warned_at",
                "suspended_at",
                "suspension_reason",
                "updated_at",
            ]
        )

        ModerationAction.objects.create(
            user=user,
            action_type=ModerationActionType.REACTIVATED,
            reason="Admin reactivated account",
            admin_user=admin_user,
            metadata={"before_status": before_status},
        )

        log_admin_action(
            admin_user=admin_user,
            action="USER_REACTIVATED",
            target_type="User",
            target_id=str(user.id),
            details={"before_status": before_status},
            ip_address=ip_address,
        )

        _notify_user_moderation(user, "reactivated", "Your account has been reactivated.")

        logger.info(
            "user_reactivated",
            extra={"user_id": user_id, "admin_id": str(admin_user.id)},
        )

        return {"success": True, "user": _user_to_dict(user)}


# ─────────────────────────────────────────
# Private helpers
# ─────────────────────────────────────────


def _user_to_dict(user) -> dict:
    """Convert User model to admin-facing dict."""
    posts_count = getattr(user, "posts_count", 0)

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
        "posts_count": posts_count,
        "warned_at": user.warned_at.isoformat() if user.warned_at else None,
        "suspended_at": user.suspended_at.isoformat() if user.suspended_at else None,
        "suspension_reason": user.suspension_reason,
        "created_at": user.created_at.isoformat() if user.created_at else "",
        "last_login": user.last_login.isoformat() if user.last_login else None,
    }


def _notify_user_moderation(user, action_type: str, reason: str):
    """Send an in-app notification about a moderation action."""
    try:
        from core.notifications.services import create_notification

        messages = {
            "warned": f"Your account has been warned: {reason}",
            "suspended": f"Your account has been suspended: {reason}",
            "reactivated": "Your account has been reactivated. Welcome back!",
        }
        message = messages.get(action_type, f"Account update: {reason}")

        create_notification(
            user_id=user.id,
            type_str="admin_announcement",
            reference_id=user.id,
            reference_type="User",
            message=message,
        )
    except Exception:
        logger.warning("Failed to send moderation notification", exc_info=True)

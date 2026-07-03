"""
User Management service — admin operations for user moderation.

All mutations are atomic with audit logging. Token revocation on suspend.
"""

import logging
from datetime import datetime, timezone

from django.db import transaction
from django.db.models import Count, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce

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
            and summary counts (total, active, warned, suspended, inactive, deleted).
        """
        from core.moderation.models import Report
        from core.users.models import User, UserLifecycleState, UserStatus

        page_size = min(page_size, 50)
        offset = (page - 1) * page_size

        # Count of reports this user has filed. Uses a correlated subquery
        # (not a second JOIN + Count) so it does not multiply the posts_count
        # aggregate below via a cartesian join. The alias avoids `submitted_reports`,
        # which is already the reverse accessor for CircleReport.reporter.
        submitted_reports_sq = (
            Report.objects.filter(reporter_id=OuterRef("pk"))
            .order_by()
            .values("reporter_id")
            .annotate(total=Count("id"))
            .values("total")
        )

        qs = User.all_objects.select_related("account_deletion_request").annotate(
            posts_count=Count("posts", filter=Q(posts__deleted_at__isnull=True)),
            submitted_report_count=Coalesce(Subquery(submitted_reports_sq), 0),
        )

        if search:
            qs = qs.filter(
                Q(username__icontains=search)
                | Q(full_name__icontains=search)
                | Q(email__icontains=search)
            )

        if status_filter:
            normalized_status = status_filter.strip().lower()
            if normalized_status == "deleted":
                qs = qs.filter(deleted_at__isnull=False)
            elif normalized_status in {
                UserLifecycleState.DEACTIVATED,
                UserLifecycleState.PENDING_DELETION,
            }:
                qs = qs.filter(lifecycle_state=normalized_status)
            elif normalized_status == "inactive":
                qs = qs.filter(deleted_at__isnull=True, is_active=False)
            else:
                qs = qs.filter(status=normalized_status, deleted_at__isnull=True)

        total_count = qs.count()
        users = list(qs.order_by("-created_at")[offset : offset + page_size])

        # Summary counts (single query with conditional aggregation)
        summary = User.all_objects.aggregate(
            total=Count("id"),
            active=Count(
                "id",
                filter=Q(deleted_at__isnull=True, is_active=True, status=UserStatus.ACTIVE),
            ),
            warned=Count(
                "id",
                filter=Q(deleted_at__isnull=True, is_active=True, status=UserStatus.WARNED),
            ),
            suspended=Count(
                "id",
                filter=Q(deleted_at__isnull=True, status=UserStatus.SUSPENDED),
            ),
            inactive=Count("id", filter=Q(deleted_at__isnull=True, is_active=False)),
            deleted=Count("id", filter=Q(deleted_at__isnull=False)),
            deactivated=Count(
                "id",
                filter=Q(lifecycle_state=UserLifecycleState.DEACTIVATED),
            ),
            pending_deletion=Count(
                "id",
                filter=Q(lifecycle_state=UserLifecycleState.PENDING_DELETION),
            ),
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

        reason = _validate_reason(reason)
        user = User.objects.select_for_update().filter(id=user_id, deleted_at__isnull=True).first()

        if not user:
            raise AdminError(
                message="User not found.",
                code=ErrorCode.USER_NOT_FOUND,
            )

        # Role-hierarchy guard: admins cannot moderate other admin accounts.
        # This prevents privilege escalation where a rogue admin locks out founders.
        if user.is_admin:
            raise AdminError(
                message="Cannot apply moderation actions to an administrator account.",
                code=ErrorCode.PERMISSION_DENIED,
            )

        if user.status == UserStatus.WARNED:
            raise AdminError(
                message="User is already warned.",
                code=ErrorCode.USER_ALREADY_WARNED,
            )

        _ensure_action_available(user, "warn")

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
        _queue_moderation_email(user, "warned", reason)

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
        from core.users.models import User, UserStatus

        reason = _validate_reason(reason)
        user = User.objects.select_for_update().filter(id=user_id, deleted_at__isnull=True).first()

        if not user:
            raise AdminError(
                message="User not found.",
                code=ErrorCode.USER_NOT_FOUND,
            )

        # Role-hierarchy guard: admins cannot moderate other admin accounts.
        if user.is_admin:
            raise AdminError(
                message="Cannot apply moderation actions to an administrator account.",
                code=ErrorCode.PERMISSION_DENIED,
            )

        if user.status == UserStatus.SUSPENDED:
            raise AdminError(
                message="User is already suspended.",
                code=ErrorCode.USER_ALREADY_SUSPENDED,
            )

        _ensure_action_available(user, "suspend")

        before_status = user.status

        user.status = UserStatus.SUSPENDED
        user.suspended_at = datetime.now(timezone.utc)
        user.suspension_reason = reason
        user.save(update_fields=["status", "suspended_at", "suspension_reason", "updated_at"])

        # Revoke ALL user tokens and device tokens (forces logout from all devices).
        _revoke_user_sessions(user.id, delete_device_tokens=False)

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
        _queue_moderation_email(user, "suspended", reason)

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
        from core.users.models import User, UserLifecycleState

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

        # Role-hierarchy guard: admins cannot delete other admin accounts.
        if user.is_admin:
            raise AdminError(
                message="Cannot apply moderation actions to an administrator account.",
                code=ErrorCode.PERMISSION_DENIED,
            )

        _ensure_action_available(user, "delete")

        # Soft delete
        deletion_time = datetime.now(timezone.utc)
        user.deleted_at = deletion_time
        user.is_active = False
        user.lifecycle_state = UserLifecycleState.DELETED
        user.save(update_fields=["deleted_at", "is_active", "lifecycle_state", "updated_at"])
        audit_details = _build_minimal_user_audit_details(user, deleted_at=deletion_time)

        # Revoke all tokens and device tokens.
        _revoke_user_sessions(user.id, delete_device_tokens=True)

        ModerationAction.objects.create(
            user=user,
            action_type=ModerationActionType.DELETED,
            reason="Admin deleted account",
            admin_user=admin_user,
            metadata=audit_details.copy(),
        )

        log_admin_action(
            admin_user=admin_user,
            action="USER_DELETED",
            target_type="User",
            target_id=str(user.id),
            details=audit_details.copy(),
            ip_address=ip_address,
        )

        logger.info(
            "user_deleted",
            extra={"user_id": user_id, "admin_id": str(admin_user.id)},
        )

        return {"success": True}

    @staticmethod
    @transaction.atomic
    def permanently_delete_user(
        user_id: str,
        reason: str,
        confirmation_text: str,
        acknowledge_permanent_deletion: bool,
        admin_user,
        ip_address: str = "",
    ) -> dict:
        """Permanently remove a user's visible data and anonymize credentials.

        This keeps the user row as an anonymized tombstone so historical FK
        constraints remain valid, but the old email/login identity can no longer
        authenticate or be recovered.
        """
        from core.users.models import User

        reason = _validate_reason(reason)

        if str(admin_user.id) == user_id:
            raise AdminError(
                message="Cannot delete your own account.",
                code=ErrorCode.USER_CANNOT_DELETE_SELF,
            )

        if not acknowledge_permanent_deletion:
            raise AdminError(
                message="Permanent deletion acknowledgement is required.",
                code=ErrorCode.VALIDATION_ERROR,
            )

        user = User.objects.select_for_update().filter(id=user_id, deleted_at__isnull=True).first()

        if not user:
            raise AdminError(
                message="User not found.",
                code=ErrorCode.USER_NOT_FOUND,
            )

        if user.is_admin:
            raise AdminError(
                message="Cannot apply moderation actions to an administrator account.",
                code=ErrorCode.PERMISSION_DENIED,
            )

        _ensure_action_available(user, "delete")

        # Validate against the locked current email before any anonymization.
        if confirmation_text != user.email:
            raise AdminError(
                message="Confirmation text must match the user's current email.",
                code=ErrorCode.VALIDATION_ERROR,
            )

        now = datetime.now(timezone.utc)
        before_status = user.status
        user_id_str = str(user.id)

        _revoke_user_sessions(user.id, delete_device_tokens=True)
        from core.users.account_lifecycle import delete_user_gcs_objects

        delete_user_gcs_objects(user)
        _remove_or_hide_user_data(user, now)

        # Moderation history tied directly to the deleted identity is removed;
        # the immutable admin audit log below keeps a sanitized compliance trail.
        from core.admin_dashboard.models import ModerationAction

        ModerationAction.objects.filter(user=user).delete()

        from core.users.account_lifecycle import anonymize_user_for_permanent_delete

        anonymize_user_for_permanent_delete(user, now)

        log_admin_action(
            admin_user=admin_user,
            action="USER_PERMANENTLY_DELETED",
            target_type="User",
            target_id=user_id_str,
            details={
                "reason": reason,
                "before_status": before_status,
                "deleted_user_id": user_id_str,
                "identity_anonymized": True,
            },
            ip_address=ip_address,
        )

        logger.info(
            "user_permanently_deleted",
            extra={"user_id": user_id_str, "admin_id": str(admin_user.id)},
        )

        return {"success": True}

    @staticmethod
    @transaction.atomic
    def reactivate_user(user_id: str, admin_user, ip_address: str = "") -> dict:
        """Reactivate a suspended user. Resets status to ACTIVE."""
        from core.admin_dashboard.models import ModerationAction, ModerationActionType
        from core.users.models import User, UserLifecycleState, UserStatus

        user = (
            User.all_objects.select_for_update().filter(id=user_id, deleted_at__isnull=True).first()
        )

        if not user:
            raise AdminError(
                message="User not found.",
                code=ErrorCode.USER_NOT_FOUND,
            )

        _ensure_action_available(user, "reactivate")

        before_status = user.status

        user.status = UserStatus.ACTIVE
        user.warned_at = None
        user.suspended_at = None
        user.suspension_reason = ""
        user.is_active = True
        user.lifecycle_state = UserLifecycleState.ACTIVE
        user.save(
            update_fields=[
                "status",
                "warned_at",
                "suspended_at",
                "suspension_reason",
                "is_active",
                "lifecycle_state",
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
        _queue_moderation_email(user, "reactivated", "")

        logger.info(
            "user_reactivated",
            extra={"user_id": user_id, "admin_id": str(admin_user.id)},
        )

        return {"success": True, "user": _user_to_dict(user)}


# ─────────────────────────────────────────
# Private helpers
# ─────────────────────────────────────────


def _build_minimal_user_audit_details(user, *, deleted_at) -> dict:
    """Return the minimum deletion audit payload needed for traceability."""
    return {
        "subject_user_id": str(user.id),
        "status_before": user.status,
        "deleted_at": deleted_at.isoformat(),
    }


def redact_legacy_user_snapshot_payloads(*, dry_run: bool = True) -> dict[str, int]:
    """Remove legacy user_snapshot payloads from moderation and audit records."""
    from core.admin_dashboard.models import AdminAuditLog, ModerationAction

    redacted_audit_logs = _redact_json_snapshot_rows(
        AdminAuditLog.objects.order_by("id"),
        field_name="details",
        subject_id_getter=lambda row: row.target_id,
        dry_run=dry_run,
    )
    redacted_moderation_actions = _redact_json_snapshot_rows(
        ModerationAction.objects.select_related("user").order_by("id"),
        field_name="metadata",
        subject_id_getter=lambda row: str(row.user_id),
        dry_run=dry_run,
    )

    return {
        "redacted_audit_logs": redacted_audit_logs,
        "redacted_moderation_actions": redacted_moderation_actions,
    }


def _redact_json_snapshot_rows(
    queryset, *, field_name: str, subject_id_getter, dry_run: bool
) -> int:
    """Strip user_snapshot payloads from JSONField-backed rows."""
    redacted_count = 0

    for row in queryset.iterator():
        payload = getattr(row, field_name, None)
        if not isinstance(payload, dict) or "user_snapshot" not in payload:
            continue

        sanitized_payload = dict(payload)
        sanitized_payload.pop("user_snapshot", None)
        sanitized_payload.setdefault("subject_user_id", subject_id_getter(row))
        sanitized_payload.setdefault("legacy_snapshot_redacted", True)

        redacted_count += 1
        if not dry_run:
            type(row).objects.filter(pk=row.pk).update(**{field_name: sanitized_payload})

    return redacted_count


def _user_to_dict(user) -> dict:
    """Convert User model to admin-facing dict."""
    posts_count = getattr(user, "posts_count", 0)
    submitted_reports = getattr(user, "submitted_report_count", 0)

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
        "warned_at": user.warned_at.isoformat() if user.warned_at else None,
        "suspended_at": user.suspended_at.isoformat() if user.suspended_at else None,
        "suspension_reason": user.suspension_reason,
        "created_at": user.created_at.isoformat() if user.created_at else "",
        "last_login": user.last_login.isoformat() if user.last_login else None,
        "available_actions": _available_actions(user),
    }


def _notify_user_moderation(user, action_type: str, reason: str):
    """Send an in-app notification about a moderation action."""
    try:
        from core.notifications.services import create_notification

        title, message = _moderation_notification_copy(action_type, reason)

        create_notification(
            user_id=user.id,
            type_str="admin_announcement",
            reference_id=user.id,
            reference_type="User",
            title=title,
            message=message,
            respect_preferences=False,
            bypass_duplicate_check=True,
        )
    except Exception:
        logger.warning("Failed to send moderation notification", exc_info=True)


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
        return []
    if user.lifecycle_state == UserLifecycleState.PENDING_DELETION:
        return []
    if not getattr(user, "is_active", True):
        return ["reactivate", "delete"]
    if user.status == UserStatus.SUSPENDED:
        return ["reactivate", "delete"]
    if user.status == UserStatus.WARNED:
        return ["suspend", "delete"]
    return ["warn", "suspend", "delete"]


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


def _revoke_user_sessions(user_id, delete_device_tokens: bool) -> None:
    from core.authentication.tokens import TokenService
    from core.notifications.models import DeviceToken

    TokenService.revoke_all_user_tokens(str(user_id))
    tokens = DeviceToken.objects.filter(user_id=user_id)
    if delete_device_tokens:
        tokens.delete()
    else:
        tokens.update(is_active=False)


def _moderation_notification_copy(action_type: str, reason: str) -> tuple[str, str]:
    if action_type == "warned":
        return (
            "Community Warning",
            "We noticed activity on your account that may violate Ziona's community "
            f'guidelines.\n\nReason:\n"{reason}"\n\n'
            "Please review our guidelines and avoid repeated violations.",
        )

    if action_type == "suspended":
        return (
            "Account Suspended",
            "Your Ziona account has been suspended.\n\n"
            f'Reason:\n"{reason}"\n\n'
            "If you believe this was a mistake, contact support@ziona.app.",
        )

    if action_type == "reactivated":
        return (
            "Account Reactivated",
            "Your Ziona account has been reactivated and you can now log in again.",
        )

    return ("Ziona Account Update", reason)


def _moderation_email_copy(action_type: str, reason: str) -> tuple[str, str]:
    if action_type == "warned":
        return (
            "Community Warning",
            "Hello,\n\n"
            "We noticed activity on your account that may violate Ziona's community "
            "guidelines.\n\n"
            f'Reason for warning:\n"{reason}"\n\n'
            "This warning does not restrict your account access at this time.\n"
            "Please review our community guidelines and avoid repeated violations to "
            "maintain a safe and faith-aligned environment for everyone.\n\n"
            "If you believe this was sent in error, you can contact us at "
            "support@ziona.app.\n\n"
            "- Ziona Team",
        )

    if action_type == "suspended":
        return (
            "Your Ziona Account Has Been Suspended",
            "Hello,\n\n"
            "Your Ziona account has been suspended due to a violation of our community "
            "guidelines.\n\n"
            f'Reason for suspension:\n"{reason}"\n\n'
            "While suspended, you will not be able to interact with content, post, "
            "comment, or access your account.\n\n"
            "If you believe this action was taken in error or would like to appeal, "
            "please contact:\nsupport@ziona.app\n\n"
            "- Ziona Team",
        )

    if action_type == "reactivated":
        return (
            "Your Ziona Account Has Been Reactivated",
            "Hello,\n\n"
            "Your Ziona account has been reactivated and you can now log in again.\n\n"
            "Please continue to follow our community guidelines to help maintain a "
            "safe and respectful environment for everyone.\n\n"
            "- Ziona Team",
        )

    return ("Ziona Account Update", reason)


def _queue_moderation_email(user, action_type: str, reason: str) -> None:
    if not user.email:
        return

    email = user.email
    subject, message = _moderation_email_copy(action_type, reason)

    def _send():
        try:
            from django.conf import settings

            from core.shared.tasks.email_tasks import queue_email_delivery

            queue_email_delivery(
                subject=subject,
                message=message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email],
                email_kind=f"moderation_{action_type}",
            )
        except Exception:
            logger.warning(
                "Failed to queue moderation email",
                extra={"user_id": str(user.id), "action_type": action_type},
                exc_info=True,
            )

    transaction.on_commit(_send)


def _remove_or_hide_user_data(user, now) -> None:
    """Remove user-owned side records and hide visible user-generated content."""
    from core.circles.models import (
        Anchor,
        AnchorEngagement,
        AnchorResponse,
        AnchorResponseReaction,
        Circle,
        CircleMembership,
        CirclePost,
        CirclePostComment,
        CirclePostCommentLike,
        CirclePostEngagement,
        CircleReport,
        HiddenCircleContent,
    )
    from core.engagement.models import (
        BookmarkFolder,
        Comment,
        CommentLike,
        HiddenComment,
        HiddenPost,
        Like,
        Save,
        Share,
    )
    from core.follows.models import Follow
    from core.media.models import MediaFile
    from core.moderation.models import Report
    from core.notifications.models import Notification, NotificationPreference
    from core.posts.models import Post
    from core.users.models import UserInterest

    CircleMembership.objects.filter(user=user).delete()
    Follow.objects.filter(Q(follower=user) | Q(following=user)).delete()
    UserInterest.objects.filter(user=user).delete()

    Like.objects.filter(Q(user=user) | Q(post__user=user)).delete()
    CommentLike.objects.filter(Q(user=user) | Q(comment__user=user)).delete()
    Save.objects.filter(Q(user=user) | Q(post__user=user)).delete()
    BookmarkFolder.objects.filter(user=user).delete()
    Share.objects.filter(Q(user=user) | Q(post__user=user)).delete()
    Share.objects.filter(recipient=user).update(recipient=None)
    HiddenComment.objects.filter(Q(user=user) | Q(comment__user=user)).delete()
    HiddenPost.objects.filter(Q(user=user) | Q(post__user=user)).delete()
    HiddenCircleContent.objects.filter(user=user).delete()

    AnchorEngagement.objects.filter(Q(user=user) | Q(anchor__created_by=user)).delete()
    AnchorResponseReaction.objects.filter(Q(user=user) | Q(response__user=user)).delete()
    CirclePostEngagement.objects.filter(Q(user=user) | Q(post__user=user)).delete()
    CirclePostCommentLike.objects.filter(Q(user=user) | Q(comment__user=user)).delete()

    AnchorResponse.objects.filter(user=user, deleted_at__isnull=True).update(
        content="",
        media_url="",
        media_type="",
        deleted_at=now,
    )
    CirclePostComment.objects.filter(user=user, deleted_at__isnull=True).update(
        text="",
        deleted_at=now,
    )
    CirclePost.objects.filter(user=user, deleted_at__isnull=True).update(
        text="",
        image_url="",
        media_url="",
        deleted_at=now,
    )
    Comment.all_objects.filter(user=user, deleted_at__isnull=True).update(
        text="",
        mentioned_users=[],
        deleted_at=now,
    )
    Post.all_objects.filter(user=user, deleted_at__isnull=True).update(
        caption="",
        media_count=0,
        deleted_at=now,
    )
    Anchor.objects.filter(created_by=user, deleted_at__isnull=True).update(
        content="",
        media_url="",
        anchor_image="",
        anchor_video="",
        anchor_thumbnail="",
        background_image="",
        anchor_text="",
        anchor_verse="",
        anchor_image_text="",
        deleted_at=now,
    )

    Circle.objects.filter(created_by=user).update(created_by=None)
    Anchor.objects.filter(created_by=user).update(created_by=None)

    CircleReport.objects.filter(reporter=user).delete()
    CircleReport.objects.filter(resolved_by=user).update(resolved_by=None)
    Report.objects.filter(reporter=user).delete()
    Report.objects.filter(reviewed_by=user).update(reviewed_by=None)

    Notification.objects.filter(Q(user=user) | Q(sender=user)).delete()
    NotificationPreference.objects.filter(user=user).delete()
    MediaFile.objects.filter(user=user).delete()

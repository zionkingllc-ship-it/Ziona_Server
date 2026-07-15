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

from core.admin_dashboard.moderation_notifications import (  # noqa: E402,F401
    _moderation_email_copy,
    _moderation_notification_copy,
    _notify_user_moderation,
    _queue_moderation_email,
)
from core.admin_dashboard.user_moderation_rules import (  # noqa: E402,F401
    _account_state,
    _available_actions,
    _ensure_action_available,
    _user_to_dict,
    _validate_reason,
)
from core.admin_dashboard.user_snapshot_redaction import (  # noqa: E402,F401
    _build_minimal_user_audit_details,
    _redact_json_snapshot_rows,
    redact_legacy_user_snapshot_payloads,
)
from core.admin_dashboard.user_teardown import (  # noqa: E402,F401
    _remove_or_hide_user_data,
    _revoke_user_sessions,
)


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

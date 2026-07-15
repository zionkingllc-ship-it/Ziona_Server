"""Admin user management (list, warn, suspend, delete, reactivate).

Split from the former core/admin_dashboard/schema.py (no contract change).
"""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from core.admin_dashboard.permissions import admin_required
from core.shared.types import ErrorType


@strawberry.type
class AdminUserType:
    """Admin-facing user representation."""

    id: str
    username: str
    email: str
    full_name: str = strawberry.field(name="fullName")
    avatar_url: str = strawberry.field(name="avatarUrl")
    bio: str
    status: str
    account_state: str = strawberry.field(name="accountState")
    lifecycle_state: str = strawberry.field(name="lifecycleState")
    deletion_status: str | None = strawberry.field(name="deletionStatus", default=None)
    deletion_requested_at: str | None = strawberry.field(name="deletionRequestedAt", default=None)
    deletion_scheduled_for: str | None = strawberry.field(name="deletionScheduledFor", default=None)
    role: str
    is_email_verified: bool = strawberry.field(name="isEmailVerified")
    is_active: bool = strawberry.field(name="isActive")
    deleted_at: str | None = strawberry.field(name="deletedAt", default=None)
    posts_count: int = strawberry.field(name="postsCount")
    submitted_reports: int = strawberry.field(name="submittedReports", default=0)
    warned_at: str | None = strawberry.field(name="warnedAt", default=None)
    suspended_at: str | None = strawberry.field(name="suspendedAt", default=None)
    suspension_reason: str = strawberry.field(name="suspensionReason", default="")
    available_actions: list[str] = strawberry.field(name="availableActions", default_factory=list)
    created_at: str = strawberry.field(name="createdAt")
    last_login: str | None = strawberry.field(name="lastLogin", default=None)


@strawberry.type
class UserSummaryType:
    """Summary counts for user management."""

    total: int
    active: int
    warned: int
    suspended: int
    inactive: int = 0
    deleted: int = 0
    deactivated: int = 0
    pending_deletion: int = strawberry.field(name="pendingDeletion", default=0)


@strawberry.type
class AdminUsersPaginatedType:
    """Paginated users response."""

    users: list[AdminUserType]
    total_count: int = strawberry.field(name="totalCount")
    page: int
    page_size: int = strawberry.field(name="pageSize")
    total_pages: int = strawberry.field(name="totalPages")
    summary: UserSummaryType


@strawberry.type
class ModerationActionPayload:
    """Response for user moderation actions."""

    success: bool
    user: AdminUserType | None = None
    error: ErrorType | None = None


def _map_user(data: dict) -> AdminUserType:
    return AdminUserType(
        id=data["id"],
        username=data["username"],
        email=data["email"],
        full_name=data["full_name"],
        avatar_url=data["avatar_url"],
        bio=data["bio"],
        status=data["status"],
        account_state=data.get("account_state", data["status"]),
        lifecycle_state=data.get("lifecycle_state", "active"),
        deletion_status=data.get("deletion_status"),
        deletion_requested_at=data.get("deletion_requested_at"),
        deletion_scheduled_for=data.get("deletion_scheduled_for"),
        role=data["role"],
        is_email_verified=data["is_email_verified"],
        is_active=data.get("is_active", True),
        deleted_at=data.get("deleted_at"),
        posts_count=data["posts_count"],
        submitted_reports=data.get("submitted_reports", 0),
        warned_at=data.get("warned_at"),
        suspended_at=data.get("suspended_at"),
        suspension_reason=data.get("suspension_reason", ""),
        available_actions=data.get("available_actions", []),
        created_at=data["created_at"],
        last_login=data.get("last_login"),
    )


@strawberry.type
class UsersAdminQueries:
    @strawberry.field(name="adminUsers", description="List users with search and filter.")
    @admin_required
    def admin_users(
        self,
        info: Info,
        search: str = "",
        status: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> AdminUsersPaginatedType:
        from core.admin_dashboard.user_services import UserManagementService

        result = UserManagementService.list_users(
            search=search,
            status_filter=status,
            page=page,
            page_size=page_size,
        )

        return AdminUsersPaginatedType(
            users=[_map_user(u) for u in result["users"]],
            total_count=result["total_count"],
            page=result["page"],
            page_size=result["page_size"],
            total_pages=result["total_pages"],
            summary=UserSummaryType(**result["summary"]),
        )


@strawberry.type
class UsersAdminMutations:
    @strawberry.mutation(name="warnUser", description="Warn a user (admin only).")
    @admin_required
    def warn_user(self, info: Info, user_id: str, reason: str) -> ModerationActionPayload:
        from core.admin_dashboard.user_services import UserManagementService
        from core.shared.exceptions import AdminError

        admin_user = info.context.admin_user
        ip = getattr(info.context, "admin_ip", "")

        try:
            result = UserManagementService.warn_user(
                user_id=user_id,
                reason=reason,
                admin_user=admin_user,
                ip_address=ip,
            )
            return ModerationActionPayload(success=True, user=_map_user(result["user"]))
        except AdminError as e:
            return ModerationActionPayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )

    @strawberry.mutation(name="suspendUser", description="Suspend a user (admin only).")
    @admin_required
    def suspend_user(self, info: Info, user_id: str, reason: str) -> ModerationActionPayload:
        from core.admin_dashboard.user_services import UserManagementService
        from core.shared.exceptions import AdminError

        admin_user = info.context.admin_user
        ip = getattr(info.context, "admin_ip", "")

        try:
            result = UserManagementService.suspend_user(
                user_id=user_id,
                reason=reason,
                admin_user=admin_user,
                ip_address=ip,
            )
            return ModerationActionPayload(success=True, user=_map_user(result["user"]))
        except AdminError as e:
            return ModerationActionPayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )

    @strawberry.mutation(name="deleteUser", description="Soft-delete a user (admin only).")
    @admin_required
    def delete_user(self, info: Info, user_id: str) -> ModerationActionPayload:
        from core.admin_dashboard.user_services import UserManagementService
        from core.shared.exceptions import AdminError

        admin_user = info.context.admin_user
        ip = getattr(info.context, "admin_ip", "")

        try:
            UserManagementService.delete_user(
                user_id=user_id,
                admin_user=admin_user,
                ip_address=ip,
            )
            return ModerationActionPayload(success=True)
        except AdminError as e:
            return ModerationActionPayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )

    @strawberry.mutation(
        name="permanentlyDeleteUser",
        description="Permanently anonymize and remove a user's visible data (admin only).",
    )
    @admin_required
    def permanently_delete_user(
        self,
        info: Info,
        user_id: str,
        reason: str,
        confirmation_text: str,
        acknowledge_permanent_deletion: bool,
    ) -> ModerationActionPayload:
        from core.admin_dashboard.user_services import UserManagementService
        from core.shared.exceptions import AdminError

        admin_user = info.context.admin_user
        ip = getattr(info.context, "admin_ip", "")

        try:
            UserManagementService.permanently_delete_user(
                user_id=user_id,
                reason=reason,
                confirmation_text=confirmation_text,
                acknowledge_permanent_deletion=acknowledge_permanent_deletion,
                admin_user=admin_user,
                ip_address=ip,
            )
            return ModerationActionPayload(success=True)
        except AdminError as e:
            return ModerationActionPayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )

    @strawberry.mutation(name="reactivateUser", description="Reactivate a user (admin only).")
    @admin_required
    def reactivate_user(self, info: Info, user_id: str) -> ModerationActionPayload:
        from core.admin_dashboard.user_services import UserManagementService
        from core.shared.exceptions import AdminError

        admin_user = info.context.admin_user
        ip = getattr(info.context, "admin_ip", "")

        try:
            result = UserManagementService.reactivate_user(
                user_id=user_id,
                admin_user=admin_user,
                ip_address=ip,
            )
            return ModerationActionPayload(success=True, user=_map_user(result["user"]))
        except AdminError as e:
            return ModerationActionPayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )

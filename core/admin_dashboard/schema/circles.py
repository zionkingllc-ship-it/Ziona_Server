"""Admin circle management (CRUD, members, stats).

Split from the former core/admin_dashboard/schema.py (no contract change).
"""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from core.admin_dashboard.permissions import admin_required
from core.admin_dashboard.schema.dashboard import MetricCardType
from core.shared.types import ErrorType


@strawberry.type
class AdminCircleType:
    """Admin-facing circle representation."""

    id: str
    name: str
    description: str
    cover_image: str = strawberry.field(name="coverImage")
    profile_image_url: str = strawberry.field(name="profileImageUrl")
    banner_image: str = strawberry.field(name="bannerImage")
    status: str
    is_active: bool = strawberry.field(name="isActive")
    member_count: int = strawberry.field(name="memberCount")
    created_by_name: str = strawberry.field(name="createdByName")
    can_edit: bool = strawberry.field(name="canEdit", default=True)
    cooldown_remaining_days: int = strawberry.field(name="cooldownRemainingDays", default=0)
    last_edited_at: str | None = strawberry.field(name="lastEditedAt", default=None)
    created_at: str = strawberry.field(name="createdAt")


@strawberry.type
class AdminCirclePayload:
    """Response for circle mutations."""

    success: bool
    circle: AdminCircleType | None = None
    error: ErrorType | None = None


@strawberry.type
class AdminCircleStatsType:
    """Circle-scoped admin stats for the circle detail page."""

    member_count: int = strawberry.field(name="memberCount")
    anchor_count: int = strawberry.field(name="anchorCount")
    engagement: MetricCardType


@strawberry.type
class AdminCircleStatsPayload:
    """Response for circle-scoped admin stats."""

    success: bool
    stats: AdminCircleStatsType | None = None
    error: ErrorType | None = None


@strawberry.type
class CircleMemberType:
    """Member in a circle."""

    id: str
    username: str
    email: str
    full_name: str = strawberry.field(name="fullName")
    avatar_url: str = strawberry.field(name="avatarUrl")
    joined_at: str = strawberry.field(name="joinedAt")
    is_active: bool = strawberry.field(name="isActive")


@strawberry.type
class CircleMembersPaginatedType:
    """Paginated circle members response."""

    members: list[CircleMemberType]
    total_count: int = strawberry.field(name="totalCount")
    page: int
    page_size: int = strawberry.field(name="pageSize")
    total_pages: int = strawberry.field(name="totalPages")


@strawberry.type
class CircleSummaryType:
    total: int
    active: int
    inactive: int


@strawberry.type
class AdminCirclesPaginatedType:
    circles: list[AdminCircleType]
    total_count: int = strawberry.field(name="totalCount")
    page: int
    page_size: int = strawberry.field(name="pageSize")
    total_pages: int = strawberry.field(name="totalPages")
    summary: CircleSummaryType


def _map_circle(data: dict) -> AdminCircleType:
    return AdminCircleType(
        id=data["id"],
        name=data["name"],
        description=data["description"],
        cover_image=data["cover_image"],
        profile_image_url=data.get("profile_image_url", ""),
        banner_image=data.get("banner_image", ""),
        status=data["status"],
        is_active=data["is_active"],
        member_count=data["member_count"],
        created_by_name=data.get("created_by_name", ""),
        can_edit=data.get("can_edit", True),
        cooldown_remaining_days=data.get("cooldown_remaining_days", 0),
        last_edited_at=data.get("last_edited_at"),
        created_at=data["created_at"],
    )


@strawberry.type
class CirclesAdminQueries:
    @strawberry.field(name="adminCircles", description="List circles with search and filter.")
    @admin_required
    def admin_circles(
        self,
        info: Info,
        search: str = "",
        status: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> AdminCirclesPaginatedType:
        from core.admin_dashboard.circle_services import CircleManagementService

        result = CircleManagementService.list_circles(
            search=search,
            status_filter=status,
            page=page,
            page_size=page_size,
        )

        return AdminCirclesPaginatedType(
            circles=[_map_circle(c) for c in result["circles"]],
            total_count=result["total_count"],
            page=result["page"],
            page_size=result["page_size"],
            total_pages=result["total_pages"],
            summary=CircleSummaryType(**result["summary"]),
        )

    @strawberry.field(name="adminCircleDetail", description="Get circle detail.")
    @admin_required
    def admin_circle_detail(self, info: Info, circle_id: str) -> AdminCirclePayload:
        from core.admin_dashboard.circle_services import CircleManagementService
        from core.shared.exceptions import AdminError

        try:
            result = CircleManagementService.get_circle_detail(circle_id)
            return AdminCirclePayload(success=True, circle=_map_circle(result))
        except AdminError as e:
            return AdminCirclePayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )

    @strawberry.field(
        name="adminCircleStats",
        description="Get circle-scoped stats for the admin circle detail page.",
    )
    @admin_required
    def admin_circle_stats(self, info: Info, circle_id: str) -> AdminCircleStatsPayload:
        from core.admin_dashboard.circle_services import CircleManagementService
        from core.shared.exceptions import AdminError

        try:
            result = CircleManagementService.get_circle_stats(circle_id)
            return AdminCircleStatsPayload(
                success=True,
                stats=AdminCircleStatsType(
                    member_count=result["member_count"],
                    anchor_count=result["anchor_count"],
                    engagement=MetricCardType(**result["engagement"]),
                ),
            )
        except AdminError as e:
            return AdminCircleStatsPayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )

    @strawberry.field(name="adminCircleMembers", description="List circle members.")
    @admin_required
    def admin_circle_members(
        self,
        info: Info,
        circle_id: str,
        page: int = 1,
        page_size: int = 20,
    ) -> CircleMembersPaginatedType:
        from core.admin_dashboard.circle_services import CircleManagementService
        from core.shared.exceptions import AdminError

        try:
            result = CircleManagementService.list_circle_members(
                circle_id=circle_id,
                page=page,
                page_size=page_size,
            )
            return CircleMembersPaginatedType(
                members=[CircleMemberType(**m) for m in result["members"]],
                total_count=result["total_count"],
                page=result["page"],
                page_size=result["page_size"],
                total_pages=result["total_pages"],
            )
        except AdminError:
            return CircleMembersPaginatedType(
                members=[],
                total_count=0,
                page=1,
                page_size=page_size,
                total_pages=1,
            )


@strawberry.type
class CirclesAdminMutations:
    @strawberry.mutation(name="adminCreateCircle", description="Create a new circle (admin only).")
    @admin_required
    def admin_create_circle(
        self,
        info: Info,
        name: str,
        description: str,
        cover_image: str,
        profile_image_url: str = "",
        banner_image: str = "",
    ) -> AdminCirclePayload:
        from core.admin_dashboard.circle_services import CircleManagementService
        from core.shared.exceptions import AdminError

        admin_user = info.context.admin_user
        ip = getattr(info.context, "admin_ip", "")

        try:
            result = CircleManagementService.create_circle(
                name=name,
                description=description,
                cover_image=cover_image,
                profile_image_url=profile_image_url,
                banner_image=banner_image,
                admin_user=admin_user,
                ip_address=ip,
            )
            return AdminCirclePayload(success=True, circle=_map_circle(result))
        except AdminError as e:
            return AdminCirclePayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )

    @strawberry.mutation(
        name="adminEditCircle",
        description="Edit a circle (admin only).",
    )
    @admin_required
    def admin_edit_circle(
        self,
        info: Info,
        circle_id: str,
        name: str | None = None,
        description: str | None = None,
        cover_image: str | None = None,
        profile_image_url: str | None = None,
        banner_image: str | None = None,
    ) -> AdminCirclePayload:
        from core.admin_dashboard.circle_services import CircleManagementService
        from core.shared.exceptions import AdminError

        admin_user = info.context.admin_user
        ip = getattr(info.context, "admin_ip", "")

        try:
            result = CircleManagementService.edit_circle(
                circle_id=circle_id,
                admin_user=admin_user,
                ip_address=ip,
                name=name,
                description=description,
                cover_image=cover_image,
                profile_image_url=profile_image_url,
                banner_image=banner_image,
            )
            return AdminCirclePayload(success=True, circle=_map_circle(result))
        except AdminError as e:
            return AdminCirclePayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )

    @strawberry.mutation(name="adminActivateCircle", description="Activate a circle.")
    @admin_required
    def admin_activate_circle(self, info: Info, circle_id: str) -> AdminCirclePayload:
        from core.admin_dashboard.circle_services import CircleManagementService
        from core.shared.exceptions import AdminError

        admin_user = info.context.admin_user
        ip = getattr(info.context, "admin_ip", "")

        try:
            result = CircleManagementService.activate_circle(
                circle_id=circle_id,
                admin_user=admin_user,
                ip_address=ip,
            )
            return AdminCirclePayload(success=True, circle=_map_circle(result))
        except AdminError as e:
            return AdminCirclePayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )

    @strawberry.mutation(name="adminDeactivateCircle", description="Deactivate a circle.")
    @admin_required
    def admin_deactivate_circle(self, info: Info, circle_id: str) -> AdminCirclePayload:
        from core.admin_dashboard.circle_services import CircleManagementService
        from core.shared.exceptions import AdminError

        admin_user = info.context.admin_user
        ip = getattr(info.context, "admin_ip", "")

        try:
            result = CircleManagementService.deactivate_circle(
                circle_id=circle_id,
                admin_user=admin_user,
                ip_address=ip,
            )
            return AdminCirclePayload(success=True, circle=_map_circle(result))
        except AdminError as e:
            return AdminCirclePayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )

    @strawberry.mutation(name="adminDeleteCircle", description="Soft-delete a circle.")
    @admin_required
    def admin_delete_circle(self, info: Info, circle_id: str) -> AdminCirclePayload:
        from core.admin_dashboard.circle_services import CircleManagementService
        from core.shared.exceptions import AdminError

        admin_user = info.context.admin_user
        ip = getattr(info.context, "admin_ip", "")

        try:
            result = CircleManagementService.delete_circle(
                circle_id=circle_id,
                admin_user=admin_user,
                ip_address=ip,
            )
            return AdminCirclePayload(success=True, circle=_map_circle(result))
        except AdminError as e:
            return AdminCirclePayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )

"""Admin anchor management (create, schedule, send, cancel).

Split from the former core/admin_dashboard/schema.py (no contract change).
"""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from core.admin_dashboard.permissions import admin_required
from core.shared.types import ErrorType


@strawberry.type
class AdminAnchorType:
    """Admin-facing anchor representation."""

    id: str
    circle_id: str = strawberry.field(name="circleId")
    title: str
    content: str
    anchor_type: str = strawberry.field(name="anchorType")
    anchor_status: str = strawberry.field(name="anchorStatus")
    media_url: str = strawberry.field(name="mediaUrl")
    anchor_image: str = strawberry.field(name="anchorImage", default="")
    anchor_video: str = strawberry.field(name="anchorVideo", default="")
    anchor_thumbnail: str = strawberry.field(name="anchorThumbnail", default="")
    preview_url: str | None = strawberry.field(name="previewUrl", default=None)
    scripture_book: str = strawberry.field(name="scriptureBook", default="")
    scripture_chapter: int | None = strawberry.field(name="scriptureChapter", default=None)
    scripture_verse_start: int | None = strawberry.field(name="scriptureVerseStart", default=None)
    scripture_verse_end: int | None = strawberry.field(name="scriptureVerseEnd", default=None)
    scripture_translation: str = strawberry.field(name="scriptureTranslation", default="")
    scripture_text: str = strawberry.field(name="scriptureText", default="")
    style_data: strawberry.scalars.JSON = strawberry.field(name="styleData", default=None)
    scheduled_for: str | None = strawberry.field(name="scheduledFor", default=None)
    posted_at: str | None = strawberry.field(name="postedAt", default=None)
    published_at: str | None = strawberry.field(name="publishedAt", default=None)
    expires_at: str | None = strawberry.field(name="expiresAt", default=None)
    author_name: str = strawberry.field(name="authorName", default="")
    created_at: str = strawberry.field(name="createdAt")


@strawberry.type
class AdminAnchorsPaginatedType:
    """Paginated anchors response."""

    anchors: list[AdminAnchorType]
    total_count: int = strawberry.field(name="totalCount")
    page: int
    page_size: int = strawberry.field(name="pageSize")
    total_pages: int = strawberry.field(name="totalPages")


@strawberry.type
class AdminAnchorPayload:
    """Response for anchor mutations."""

    success: bool
    anchor: AdminAnchorType | None = None
    error: ErrorType | None = None


def _map_anchor(data: dict) -> AdminAnchorType:
    return AdminAnchorType(
        id=data["id"],
        circle_id=data["circle_id"],
        title=data["title"],
        content=data["content"],
        anchor_type=data["anchor_type"],
        anchor_status=data["anchor_status"],
        media_url=data.get("media_url", ""),
        anchor_image=data.get("anchor_image", ""),
        anchor_video=data.get("anchor_video", ""),
        anchor_thumbnail=data.get("anchor_thumbnail", ""),
        preview_url=data.get("preview_url"),
        scripture_book=data.get("scripture_book", ""),
        scripture_chapter=data.get("scripture_chapter"),
        scripture_verse_start=data.get("scripture_verse_start"),
        scripture_verse_end=data.get("scripture_verse_end"),
        scripture_translation=data.get("scripture_translation", ""),
        scripture_text=data.get("scripture_text", ""),
        style_data=data.get("style_data", {}),
        scheduled_for=data.get("scheduled_for"),
        posted_at=data.get("posted_at"),
        published_at=data.get("published_at"),
        expires_at=data.get("expires_at"),
        author_name=data.get("author_name", ""),
        created_at=data["created_at"],
    )


@strawberry.type
class AnchorsAdminQueries:
    @strawberry.field(name="adminAnchors", description="List anchors for a circle.")
    @admin_required
    def admin_anchors(
        self,
        info: Info,
        circle_id: str,
        status: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> AdminAnchorsPaginatedType:
        from core.admin_dashboard.anchor_services import AnchorManagementService

        result = AnchorManagementService.list_anchors(
            circle_id=circle_id,
            status_filter=status,
            page=page,
            page_size=page_size,
        )

        return AdminAnchorsPaginatedType(
            anchors=[_map_anchor(a) for a in result["anchors"]],
            total_count=result["total_count"],
            page=result["page"],
            page_size=result["page_size"],
            total_pages=result["total_pages"],
        )


@strawberry.type
class AnchorsAdminMutations:
    @strawberry.mutation(name="adminCreateAnchor", description="Create a draft anchor.")
    @admin_required
    def admin_create_anchor(
        self,
        info: Info,
        circle_id: str,
        anchor_type: str,
        title: str,
        content: str = "",
        scripture_book: str = "",
        scripture_chapter: int | None = None,
        scripture_verse_start: int | None = None,
        scripture_verse_end: int | None = None,
        scripture_translation: str = "KJV",
        scripture_text: str = "",
        media_url: str = "",
        anchor_image: str = "",
        anchor_video: str = "",
        anchor_thumbnail: str = "",
        style_data: strawberry.scalars.JSON | None = None,
    ) -> AdminAnchorPayload:
        from core.admin_dashboard.anchor_services import AnchorManagementService
        from core.shared.exceptions import AdminError

        admin_user = info.context.admin_user
        ip = getattr(info.context, "admin_ip", "")

        try:
            result = AnchorManagementService.create_anchor(
                circle_id=circle_id,
                anchor_type=anchor_type,
                title=title,
                content=content,
                scripture_book=scripture_book,
                scripture_chapter=scripture_chapter,
                scripture_verse_start=scripture_verse_start,
                scripture_verse_end=scripture_verse_end,
                scripture_translation=scripture_translation,
                scripture_text=scripture_text,
                media_url=media_url,
                anchor_image=anchor_image,
                anchor_video=anchor_video,
                anchor_thumbnail=anchor_thumbnail,
                style_data=style_data,
                admin_user=admin_user,
                ip_address=ip,
            )
            return AdminAnchorPayload(success=True, anchor=_map_anchor(result))
        except AdminError as e:
            return AdminAnchorPayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )

    @strawberry.mutation(name="adminScheduleAnchor", description="Schedule an anchor for posting.")
    @admin_required
    def admin_schedule_anchor(
        self,
        info: Info,
        anchor_id: str,
        scheduled_for: str,
    ) -> AdminAnchorPayload:
        from datetime import datetime as dt

        from core.admin_dashboard.anchor_services import AnchorManagementService
        from core.shared.exceptions import AdminError

        admin_user = info.context.admin_user
        ip = getattr(info.context, "admin_ip", "")

        try:
            parsed_time = dt.fromisoformat(scheduled_for)
            result = AnchorManagementService.schedule_anchor(
                anchor_id=anchor_id,
                scheduled_for=parsed_time,
                admin_user=admin_user,
                ip_address=ip,
            )
            return AdminAnchorPayload(success=True, anchor=_map_anchor(result))
        except AdminError as e:
            return AdminAnchorPayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )
        except ValueError:
            return AdminAnchorPayload(
                success=False,
                error=ErrorType(code="VALIDATION_ERROR", message="Invalid date format."),
            )

    @strawberry.mutation(name="adminSendAnchorNow", description="Post an anchor immediately.")
    @admin_required
    def admin_send_anchor_now(self, info: Info, anchor_id: str) -> AdminAnchorPayload:
        from core.admin_dashboard.anchor_services import AnchorManagementService
        from core.shared.exceptions import AdminError

        admin_user = info.context.admin_user
        ip = getattr(info.context, "admin_ip", "")

        try:
            result = AnchorManagementService.send_now(
                anchor_id=anchor_id,
                admin_user=admin_user,
                ip_address=ip,
            )
            return AdminAnchorPayload(success=True, anchor=_map_anchor(result))
        except AdminError as e:
            return AdminAnchorPayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )

    @strawberry.mutation(
        name="adminEditScheduledAnchor",
        description="Edit a scheduled anchor's content.",
    )
    @admin_required
    def admin_edit_scheduled_anchor(
        self,
        info: Info,
        anchor_id: str,
        title: str | None = None,
        content: str | None = None,
        media_url: str | None = None,
        anchor_image: str | None = None,
        anchor_video: str | None = None,
        anchor_thumbnail: str | None = None,
        scripture_book: str | None = None,
        scripture_chapter: int | None = None,
        scripture_verse_start: int | None = None,
        scripture_verse_end: int | None = None,
        scripture_translation: str | None = None,
        scripture_text: str | None = None,
    ) -> AdminAnchorPayload:
        from core.admin_dashboard.anchor_services import AnchorManagementService
        from core.shared.exceptions import AdminError

        admin_user = info.context.admin_user
        ip = getattr(info.context, "admin_ip", "")

        try:
            result = AnchorManagementService.edit_scheduled_anchor(
                anchor_id=anchor_id,
                admin_user=admin_user,
                ip_address=ip,
                title=title,
                content=content,
                media_url=media_url,
                anchor_image=anchor_image,
                anchor_video=anchor_video,
                anchor_thumbnail=anchor_thumbnail,
                scripture_book=scripture_book,
                scripture_chapter=scripture_chapter,
                scripture_verse_start=scripture_verse_start,
                scripture_verse_end=scripture_verse_end,
                scripture_translation=scripture_translation,
                scripture_text=scripture_text,
            )
            return AdminAnchorPayload(success=True, anchor=_map_anchor(result))
        except AdminError as e:
            return AdminAnchorPayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )

    @strawberry.mutation(
        name="adminCancelScheduledAnchor",
        description="Cancel a scheduled anchor.",
    )
    @admin_required
    def admin_cancel_scheduled_anchor(self, info: Info, anchor_id: str) -> AdminAnchorPayload:
        from core.admin_dashboard.anchor_services import AnchorManagementService
        from core.shared.exceptions import AdminError

        admin_user = info.context.admin_user
        ip = getattr(info.context, "admin_ip", "")

        try:
            result = AnchorManagementService.cancel_scheduled_anchor(
                anchor_id=anchor_id,
                admin_user=admin_user,
                ip_address=ip,
            )
            return AdminAnchorPayload(success=True, anchor=_map_anchor(result))
        except AdminError as e:
            return AdminAnchorPayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )

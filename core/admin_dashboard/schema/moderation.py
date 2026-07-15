"""Admin moderation queue (reports, review, restore).

Split from the former core/admin_dashboard/schema.py (no contract change).
"""

from __future__ import annotations

from enum import Enum

import strawberry
from strawberry.types import Info

from core.admin_dashboard.permissions import admin_required
from core.shared.types import ErrorType


@strawberry.enum
class ModerationActionEnum(Enum):
    DISMISS = "dismiss"
    HIDE_CONTENT = "hide_content"
    WARN_USER = "warn_user"
    DELETE_CONTENT = "delete_content"
    DELETE_AND_WARN = "delete_and_warn"


@strawberry.type
class ReporterType:
    """Reporter info embedded in a report."""

    id: str
    username: str
    avatar_url: str = strawberry.field(name="avatarUrl")


@strawberry.type
class AdminReportMediaType:
    """Media preview item for reported content."""

    url: str
    media_type: str = strawberry.field(name="mediaType")
    thumbnail_url: str = strawberry.field(name="thumbnailUrl", default="")
    order: int


@strawberry.type
class AdminReportType:
    """Admin-facing report representation."""

    id: str
    reporter: ReporterType | None = None
    target_type: str = strawberry.field(name="targetType")
    target_id: str | None = strawberry.field(name="targetId", default=None)
    post_id: str | None = strawberry.field(name="postId", default=None)
    comment_id: str | None = strawberry.field(name="commentId", default=None)
    reason: str
    description: str
    status: str
    action: str
    internal_notes: str = strawberry.field(name="internalNotes")
    content_preview: str = strawberry.field(name="contentPreview")
    content_owner: str = strawberry.field(name="contentOwner")
    content_media_url: str = strawberry.field(name="contentMediaUrl", default="")
    content_media_type: str = strawberry.field(name="contentMediaType", default="")
    content_thumbnail_url: str = strawberry.field(name="contentThumbnailUrl", default="")
    content_media: list[AdminReportMediaType] = strawberry.field(
        name="contentMedia", default_factory=list
    )
    reviewed_by_name: str = strawberry.field(name="reviewedByName")
    reviewed_at: str | None = strawberry.field(name="reviewedAt", default=None)
    created_at: str = strawberry.field(name="createdAt")


@strawberry.type
class ReportSummaryType:
    """Summary counts for reports."""

    total: int
    pending: int
    reviewed: int
    actioned: int
    dismissed: int


@strawberry.type
class AdminReportsPaginatedType:
    """Paginated reports response."""

    reports: list[AdminReportType]
    total_count: int = strawberry.field(name="totalCount")
    page: int
    page_size: int = strawberry.field(name="pageSize")
    total_pages: int = strawberry.field(name="totalPages")
    summary: ReportSummaryType


@strawberry.type
class AdminReportReviewPayload:
    """Response for report review mutation."""

    success: bool
    report: AdminReportType | None = None
    error: ErrorType | None = None


def _map_report(data: dict) -> AdminReportType:
    reporter = None
    if data.get("reporter"):
        reporter = ReporterType(
            id=data["reporter"]["id"],
            username=data["reporter"]["username"],
            avatar_url=data["reporter"].get("avatar_url", ""),
        )

    return AdminReportType(
        id=data["id"],
        reporter=reporter,
        target_type=data.get("target_type", ""),
        target_id=data.get("target_id"),
        post_id=data.get("post_id"),
        comment_id=data.get("comment_id"),
        reason=data["reason"],
        description=data.get("description", ""),
        status=data["status"],
        action=data.get("action", ""),
        internal_notes=data.get("internal_notes", ""),
        content_preview=data.get("content_preview", ""),
        content_owner=data.get("content_owner", ""),
        content_media_url=data.get("content_media_url", ""),
        content_media_type=data.get("content_media_type", ""),
        content_thumbnail_url=data.get("content_thumbnail_url", ""),
        content_media=[
            AdminReportMediaType(
                url=item["url"],
                media_type=item["media_type"],
                thumbnail_url=item.get("thumbnail_url", ""),
                order=item.get("order", 0),
            )
            for item in data.get("content_media", [])
        ],
        reviewed_by_name=data.get("reviewed_by_name", ""),
        reviewed_at=data.get("reviewed_at"),
        created_at=data["created_at"],
    )


@strawberry.type
class AdminCircleReportPreviewType:
    """Resolved preview of the reported circle content (anchor/response/circle)."""

    available: bool
    unavailable_reason: str = strawberry.field(name="unavailableReason")
    text: str
    media_url: str = strawberry.field(name="mediaUrl")
    media_type: str = strawberry.field(name="mediaType")
    thumbnail_url: str = strawberry.field(name="thumbnailUrl")


@strawberry.type
class AdminCircleReportType:
    """Admin-facing circle-content report (from circles.CircleReport)."""

    id: str
    reporter_username: str = strawberry.field(name="reporterUsername")
    circle_id: str = strawberry.field(name="circleId")
    circle_name: str = strawberry.field(name="circleName")
    target_type: str = strawberry.field(name="targetType")
    target_id: str = strawberry.field(name="targetId")
    reason: str
    status: str
    report_count: int = strawberry.field(name="reportCount")
    auto_hidden: bool = strawberry.field(name="autoHidden")
    content_preview: AdminCircleReportPreviewType = strawberry.field(name="contentPreview")
    created_at: str = strawberry.field(name="createdAt")
    resolved_at: str | None = strawberry.field(name="resolvedAt", default=None)
    resolved_by_username: str = strawberry.field(name="resolvedByUsername", default="")


@strawberry.type
class CircleReportSummaryType:
    """Summary counts for circle reports."""

    total: int
    pending: int
    resolved_kept: int = strawberry.field(name="resolvedKept")
    resolved_removed: int = strawberry.field(name="resolvedRemoved")


@strawberry.type
class AdminCircleReportsPaginatedType:
    """Paginated circle-reports response."""

    reports: list[AdminCircleReportType]
    total_count: int = strawberry.field(name="totalCount")
    page: int
    page_size: int = strawberry.field(name="pageSize")
    total_pages: int = strawberry.field(name="totalPages")
    summary: CircleReportSummaryType


def _map_circle_report(data: dict) -> AdminCircleReportType:
    preview = data["content_preview"]
    return AdminCircleReportType(
        id=data["id"],
        reporter_username=data["reporter_username"],
        circle_id=data["circle_id"],
        circle_name=data["circle_name"],
        target_type=data["target_type"],
        target_id=data["target_id"],
        reason=data["reason"],
        status=data["status"],
        report_count=data["report_count"],
        auto_hidden=data["auto_hidden"],
        content_preview=AdminCircleReportPreviewType(
            available=preview["available"],
            unavailable_reason=preview["unavailable_reason"],
            text=preview["text"],
            media_url=preview["media_url"],
            media_type=preview["media_type"],
            thumbnail_url=preview["thumbnail_url"],
        ),
        created_at=data["created_at"],
        resolved_at=data["resolved_at"],
        resolved_by_username=data["resolved_by_username"],
    )


@strawberry.type
class ModerationAdminQueries:
    @strawberry.field(
        name="adminCircleReports",
        description="List reports on circle content (anchors, responses, circles).",
    )
    @admin_required
    def admin_circle_reports(
        self,
        info: Info,
        status: str = "",
        target_type: str = "",
        circle_id: str = "",
        search: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> AdminCircleReportsPaginatedType:
        from core.admin_dashboard.circle_report_services import list_circle_reports

        result = list_circle_reports(
            status_filter=status,
            target_type_filter=target_type,
            circle_id=circle_id,
            search=search,
            page=page,
            page_size=page_size,
        )

        return AdminCircleReportsPaginatedType(
            reports=[_map_circle_report(r) for r in result["reports"]],
            total_count=result["total_count"],
            page=result["page"],
            page_size=result["page_size"],
            total_pages=result["total_pages"],
            summary=CircleReportSummaryType(**result["summary"]),
        )

    @strawberry.field(name="adminReports", description="List reports with search and filter.")
    @admin_required
    def admin_reports(
        self,
        info: Info,
        status: str = "",
        search: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> AdminReportsPaginatedType:
        from core.admin_dashboard.moderation_services import AdminModerationService

        result = AdminModerationService.list_reports(
            status_filter=status,
            search=search,
            page=page,
            page_size=page_size,
        )

        return AdminReportsPaginatedType(
            reports=[_map_report(r) for r in result["reports"]],
            total_count=result["total_count"],
            page=result["page"],
            page_size=result["page_size"],
            total_pages=result["total_pages"],
            summary=ReportSummaryType(**result["summary"]),
        )


@strawberry.type
class ModerationAdminMutations:
    @strawberry.mutation(
        name="adminReviewReport",
        description="Review a report and take action.",
    )
    @admin_required
    def admin_review_report(
        self,
        info: Info,
        report_id: str,
        action: str,
        reason: str = "",
        internal_notes: str = "",
    ) -> AdminReportReviewPayload:
        from core.admin_dashboard.moderation_services import AdminModerationService
        from core.shared.exceptions import AdminError

        admin_user = info.context.admin_user
        ip = getattr(info.context, "admin_ip", "")

        try:
            result = AdminModerationService.review_report(
                report_id=report_id,
                action=action,
                reason=reason,
                internal_notes=internal_notes,
                admin_user=admin_user,
                ip_address=ip,
            )
            return AdminReportReviewPayload(
                success=True,
                report=_map_report(result["report"]),
            )
        except AdminError as e:
            return AdminReportReviewPayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )

    @strawberry.mutation(
        name="adminRestoreContent",
        description="Restore (un-hide) content previously hidden via moderation.",
    )
    @admin_required
    def admin_restore_content(
        self,
        info: Info,
        report_id: str,
    ) -> AdminReportReviewPayload:
        from core.admin_dashboard.moderation_services import AdminModerationService
        from core.shared.exceptions import AdminError

        admin_user = info.context.admin_user
        ip = getattr(info.context, "admin_ip", "")

        try:
            result = AdminModerationService.restore_content(
                report_id=report_id,
                admin_user=admin_user,
                ip_address=ip,
            )
            return AdminReportReviewPayload(
                success=True,
                report=_map_report(result["report"]),
            )
        except AdminError as e:
            return AdminReportReviewPayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )

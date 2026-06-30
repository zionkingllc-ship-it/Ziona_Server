"""GraphQL types, queries, and mutations for the moderation domain."""

import strawberry

from core.shared.types import ErrorType
from core.users.schema import _get_authenticated_user_id


@strawberry.type
class ReportMediaPreviewType:
    """Media metadata used by admin moderation previews."""

    url: str
    media_type: str = strawberry.field(name="mediaType")
    thumbnail_url: str | None = strawberry.field(name="thumbnailUrl", default=None)
    order: int = 0
    width: int | None = None
    height: int | None = None
    duration: float | None = None


@strawberry.type
class ReportContentPreviewType:
    """Resolved reported-content preview payload for moderation UIs."""

    target_type: str = strawberry.field(name="targetType")
    target_id: str = strawberry.field(name="targetId")
    available: bool
    unavailable_reason: str | None = strawberry.field(name="unavailableReason", default=None)
    owner_id: str | None = strawberry.field(name="ownerId", default=None)
    owner_username: str | None = strawberry.field(name="ownerUsername", default=None)
    owner_name: str | None = strawberry.field(name="ownerName", default=None)
    text: str | None = None
    media: list[ReportMediaPreviewType] = strawberry.field(default_factory=list)


@strawberry.type
class ReportType:
    """A content report."""

    id: str
    reporter_id: str
    post_id: str | None = None
    comment_id: str | None = None
    reason: str
    description: str | None = None
    status: str
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    created_at: str
    content_preview: ReportContentPreviewType | None = strawberry.field(
        name="contentPreview", default=None
    )


@strawberry.type
class ReportPayload:
    """
    Response outlining execution state of a content report safely natively.

    **Authentication:** Required
    **Related operations:** report_content, review_report
    """

    success: bool = strawberry.field(description="Confirmed processing natively flag")
    report: ReportType | None = strawberry.field(
        default=None, description="Mapped explicit target UUID"
    )
    error: ErrorType | None = strawberry.field(default=None, description="Explicit error info")
    message: str | None = strawberry.field(
        default=None, description="String output detail natively"
    )
    error_code: str | None = strawberry.field(
        default=None, description="Detailed failure string identifier"
    )


@strawberry.type
class ReportListResponse:
    """
    Paginated Admin dashboard queue array natively.

    **Authentication:** Required (Admin only)
    **Related operations:** list_reports
    """

    reports: list[ReportType] = strawberry.field(description="Directly mapped queue items natively")
    next_cursor: str | None = strawberry.field(
        default=None, description="Hash mapped string continuation flag"
    )
    has_more: bool = strawberry.field(default=False, description="Volume bounds checker boolean")


@strawberry.type
class ModerationMutations:
    """Moderation domain GraphQL mutations."""

    @strawberry.mutation(
        description="File a Community Guidelines violation against an active node."
    )
    def report_content(
        self,
        info: strawberry.types.Info,
        reason: str,
        post_id: str | None = None,
        comment_id: str | None = None,
        description: str | None = None,
    ) -> ReportPayload:
        """
        Create an Admin dashboard ticket for explicit user-generated content organically natively.

        Requires EITHER a `post_id` OR `comment_id` passed accurately.

        **Authentication:** Required
        **Parameters:**
        - reason (String, required) - Violation code implicitly
        - post_id/comment_id (String, optional) - Target mapping natively
        - description (String, optional) - Extra info context
        **Returns:** ReportPayload confirming queue insertion successfully natively
        **Errors:** UNAUTHENTICATED, VALIDATION_ERROR native limits.
        """
        from core.moderation.services import ReportService
        from core.shared.exceptions import ModerationError

        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return ReportPayload(
                success=False,
                message="Authentication required",
                error_code="UNAUTHORIZED",
            )

        try:
            result = ReportService.report_content(
                reporter_id=user_id,
                reason=reason,
                post_id=post_id,
                comment_id=comment_id,
                description=description,
            )
            return ReportPayload(success=True, report=_get_report_type(result["report_id"]))
        except ModerationError as e:
            return ReportPayload(
                success=False,
                message=e.message,
                error_code=e.code,
                error=ErrorType(code=e.code, message=e.message),
            )

    @strawberry.mutation(
        description="Update specific report processing state dynamically (Admin only)."
    )
    def review_report(
        self,
        info: strawberry.types.Info,
        report_id: str,
        status: str,
        action: str | None = None,
        internal_notes: str | None = None,
    ) -> ReportPayload:
        """
        Transition report ticket workflow and execute the moderation action.

        **Authentication:** Required (User Role mapping Admin)
        **Parameters:**
        - report_id (String, required) - Valid remote ticket
        - status (String, required) - Resolution context (reviewed, actioned, dismissed)
        - action (String, optional) - Moderation action: dismiss, hide_content,
          warn_user, delete_content, delete_and_warn
        - internal_notes (String, optional) - Admin-only notes, never shown to users
        **Returns:** ReportPayload tracking transition exactly natively
        **Errors:** UNAUTHENTICATED, PERMISSION_DENIED
        """
        from core.moderation.services import ReportService
        from core.shared.exceptions import ModerationError
        from core.users.models import User

        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return ReportPayload(
                success=False,
                message="Authentication required",
                error_code="UNAUTHORIZED",
            )

        user = User.objects.filter(id=user_id).first()
        if not user or not user.is_admin:
            return ReportPayload(
                success=False,
                message="Admin access required",
                error_code="PERMISSION_DENIED",
            )

        try:
            result = ReportService.review_report(
                report_id=report_id,
                reviewer_id=user_id,
                status=status,
                action=action,
                internal_notes=internal_notes or "",
            )
            return ReportPayload(success=True, report=_get_report_type(result["report_id"]))
        except ModerationError as e:
            return ReportPayload(
                success=False,
                message=e.message,
                error_code=e.code,
                error=ErrorType(code=e.code, message=e.message),
            )


@strawberry.type
class ModerationQueries:
    """Moderation domain GraphQL queries (admin only)."""

    @strawberry.field(description="Extract paginated array list of raw Admin reports queued.")
    def list_reports(
        self,
        info: strawberry.types.Info,
        status: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> ReportListResponse:
        """
        Fetch hierarchical descending queue items for platform Admin interface seamlessly.

        **Authentication:** Required (User Role mapped Admins exclusively tightly bounded)
        **Parameters:**
        - status (String, optional) - Enum bounded implicitly
        - cursor (String, optional) - Passes dynamically
        - limit (Int, optional) - Cap limits natively securely
        **Returns:** ReportListResponse mapping items cleanly dynamically organically
        **Errors:** Fails safely yielding empty cleanly natively directly avoiding throws globally.
        """
        from core.moderation.services import ReportService
        from core.users.models import User

        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return ReportListResponse(reports=[], has_more=False)

        user = User.objects.filter(id=user_id).first()
        if not user or not user.is_admin:
            return ReportListResponse(reports=[], has_more=False)

        result = ReportService.list_reports(status=status, cursor=cursor, limit=limit)

        report_ids = [r["id"] for r in result["reports"]]
        report_map = {
            str(report.id): _report_to_type(report)
            for report in _report_queryset().filter(id__in=report_ids)
        }

        return ReportListResponse(
            reports=[report_map[r["id"]] for r in result["reports"] if r["id"] in report_map],
            next_cursor=result["next_cursor"],
            has_more=result["has_more"],
        )


def _report_queryset():
    from core.moderation.models import Report

    return Report.objects.select_related(
        "reporter",
        "post",
        "post__user",
        "comment",
        "comment__user",
        "comment__post",
        "comment__post__user",
        "reviewed_by",
    ).prefetch_related(
        "post__post_media",
        "post__media_files",
        "comment__post__post_media",
        "comment__post__media_files",
    )


def _get_report_type(report_id: str) -> ReportType:
    report = _report_queryset().get(id=report_id)
    return _report_to_type(report)


def _report_to_type(report) -> ReportType:
    return ReportType(
        id=str(report.id),
        reporter_id=str(report.reporter_id),
        post_id=str(report.post_id) if report.post_id else None,
        comment_id=str(report.comment_id) if report.comment_id else None,
        reason=report.reason,
        description=report.description,
        status=report.status,
        reviewed_by=str(report.reviewed_by_id) if report.reviewed_by_id else None,
        reviewed_at=report.reviewed_at.isoformat() if report.reviewed_at else None,
        created_at=report.created_at.isoformat(),
        content_preview=_build_content_preview(report),
    )


def _build_content_preview(report) -> ReportContentPreviewType:
    target_type = (report.target_type or ("post" if report.post_id else "comment")).lower()
    target_id = str(report.target_id or report.post_id or report.comment_id or "")

    if target_type == "comment" and report.comment:
        comment = report.comment
        owner = comment.user
        post = getattr(comment, "post", None)
        return ReportContentPreviewType(
            target_type=target_type,
            target_id=target_id,
            available=comment.deleted_at is None,
            unavailable_reason="deleted" if comment.deleted_at else None,
            owner_id=str(owner.id) if owner else None,
            owner_username=getattr(owner, "username", None),
            owner_name=_user_display_name(owner),
            text=comment.text,
            media=_post_media_preview(post) if post else [],
        )

    post = report.post
    if not post and target_type == "comment" and report.comment:
        post = report.comment.post

    if post:
        owner = post.user
        return ReportContentPreviewType(
            target_type=target_type,
            target_id=target_id,
            available=post.deleted_at is None,
            unavailable_reason="deleted" if post.deleted_at else None,
            owner_id=str(owner.id) if owner else None,
            owner_username=getattr(owner, "username", None),
            owner_name=_user_display_name(owner),
            text=post.caption or "",
            media=_post_media_preview(post),
        )

    return ReportContentPreviewType(
        target_type=target_type,
        target_id=target_id,
        available=False,
        unavailable_reason="unavailable",
    )


def _post_media_preview(post) -> list[ReportMediaPreviewType]:
    media_items = []
    for media in post.post_media.all():
        media_items.append(
            ReportMediaPreviewType(
                url=media.media_url,
                media_type=media.media_type,
                thumbnail_url=media.thumbnail_url,
                order=media.order,
                width=media.width,
                height=media.height,
                duration=media.duration,
            )
        )

    if media_items:
        return media_items

    for index, media in enumerate(post.media_files.all()):
        media_items.append(
            ReportMediaPreviewType(
                url=media.url,
                media_type=media.media_type,
                thumbnail_url=media.thumbnail_url,
                order=index,
                width=media.width,
                height=media.height,
                duration=media.duration,
            )
        )
    return media_items


def _user_display_name(user) -> str | None:
    if not user:
        return None
    return user.full_name or user.username or user.email

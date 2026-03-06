"""GraphQL types, queries, and mutations for the moderation domain."""


import strawberry

from core.users.schema import _get_authenticated_user_id


@strawberry.type
class ReportPayload:
    """Response for report mutations."""

    success: bool
    report_id: str | None = None
    message: str | None = None
    error_code: str | None = None


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


@strawberry.type
class ReportListResponse:
    """Paginated reports list response."""

    reports: list[ReportType]
    next_cursor: str | None = None
    has_more: bool = False


@strawberry.type
class ModerationMutations:
    """Moderation domain GraphQL mutations."""

    @strawberry.mutation(description="Report a post or comment")
    def report_content(
        self,
        info: strawberry.types.Info,
        reason: str,
        post_id: str | None = None,
        comment_id: str | None = None,
        description: str | None = None,
    ) -> ReportPayload:
        """Report content for moderation."""
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
            return ReportPayload(success=True, report_id=result["report_id"])
        except ModerationError as e:
            return ReportPayload(success=False, message=e.message, error_code=e.code)

    @strawberry.mutation(description="Review a report (admin only)")
    def review_report(
        self,
        info: strawberry.types.Info,
        report_id: str,
        status: str,
    ) -> ReportPayload:
        """Review and update a report's status."""
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
            )
            return ReportPayload(success=True, report_id=result["report_id"])
        except ModerationError as e:
            return ReportPayload(success=False, message=e.message, error_code=e.code)


@strawberry.type
class ModerationQueries:
    """Moderation domain GraphQL queries (admin only)."""

    @strawberry.field(description="List content reports (admin only)")
    def list_reports(
        self,
        info: strawberry.types.Info,
        status: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> ReportListResponse:
        """List reports for admin review."""
        from core.moderation.services import ReportService
        from core.users.models import User

        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return ReportListResponse(reports=[], has_more=False)

        user = User.objects.filter(id=user_id).first()
        if not user or not user.is_admin:
            return ReportListResponse(reports=[], has_more=False)

        result = ReportService.list_reports(status=status, cursor=cursor, limit=limit)

        return ReportListResponse(
            reports=[
                ReportType(
                    id=r["id"],
                    reporter_id=r["reporter_id"],
                    post_id=r["post_id"],
                    comment_id=r["comment_id"],
                    reason=r["reason"],
                    description=r["description"],
                    status=r["status"],
                    reviewed_by=r["reviewed_by"],
                    reviewed_at=r["reviewed_at"],
                    created_at=r["created_at"],
                )
                for r in result["reports"]
            ],
            next_cursor=result["next_cursor"],
            has_more=result["has_more"],
        )

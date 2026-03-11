"""GraphQL types, queries, and mutations for the moderation domain."""


import strawberry

from core.users.schema import _get_authenticated_user_id


@strawberry.type
class ReportPayload:
    """
    Response outlining execution state of a content report safely natively.

    **Authentication:** Required
    **Related operations:** report_content, review_report
    """

    success: bool = strawberry.field(description="Confirmed processing natively flag")
    report_id: str | None = strawberry.field(
        default=None, description="Mapped explicit target UUID"
    )
    message: str | None = strawberry.field(
        default=None, description="String output detail natively"
    )
    error_code: str | None = strawberry.field(
        default=None, description="Detailed failure string identifier"
    )


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
            return ReportPayload(success=True, report_id=result["report_id"])
        except ModerationError as e:
            return ReportPayload(success=False, message=e.message, error_code=e.code)

    @strawberry.mutation(
        description="Update specific report processing state dynamically (Admin only)."
    )
    def review_report(
        self,
        info: strawberry.types.Info,
        report_id: str,
        status: str,
    ) -> ReportPayload:
        """
        Transition report ticket workflow explicitly dynamically.

        **Authentication:** Required (User Role mapping Admin)
        **Parameters:**
        - report_id (String, required) - Valid remote ticket
        - status (String, required) - Resolution context natively
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
            )
            return ReportPayload(success=True, report_id=result["report_id"])
        except ModerationError as e:
            return ReportPayload(success=False, message=e.message, error_code=e.code)


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

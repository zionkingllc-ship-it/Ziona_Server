"""
Report/moderation service — business logic for content reporting.

Handles user-submitted content reports and admin report review.
"""

import logging
from datetime import datetime, timezone

from core.moderation.models import Report, ReportReason, ReportStatus
from core.shared.decorators import rate_limit
from core.shared.exceptions import ErrorCode, ModerationError

logger = logging.getLogger("core.moderation")


class ReportService:
    """Service handling content reporting and moderation review."""

    @staticmethod
    @rate_limit(max_requests=5, window_seconds=300)
    def report_content(
        reporter_id: str,
        reason: str,
        post_id: str | None = None,
        comment_id: str | None = None,
        description: str | None = None,
    ) -> dict:
        """Report a post or comment.

        Args:
            reporter_id: UUID of the reporting user.
            reason: Report reason from ReportReason enum.
            post_id: UUID of the post (if reporting a post).
            comment_id: UUID of the comment (if reporting a comment).
            description: Optional free-text description (required for 'other').

        Returns:
            Dict with report_id and success status.

        Raises:
            ModerationError: If validation fails.
        """
        from core.engagement.models import Comment
        from core.posts.models import Post

        # Must have at least one target
        if not post_id and not comment_id:
            raise ModerationError(
                message="Must specify either a post or comment to report.",
                code=ErrorCode.INVALID_REPORT_TARGET,
            )

        valid_reasons = [r.value for r in ReportReason]
        if reason not in valid_reasons:
            raise ModerationError(
                message=f"Invalid report reason. Must be one of: {', '.join(valid_reasons)}.",
                code=ErrorCode.INVALID_REPORT_REASON,
            )

        if reason == ReportReason.OTHER and not description:
            raise ModerationError(
                message="Description is required when reason is 'other'.",
                code=ErrorCode.DESCRIPTION_REQUIRED,
            )

        if post_id:
            post = Post.objects.filter(id=post_id, deleted_at__isnull=True).first()
            if not post:
                raise ModerationError(
                    message="Post not found.",
                    code=ErrorCode.POST_NOT_FOUND,
                )

        if comment_id:
            comment = Comment.objects.filter(id=comment_id, deleted_at__isnull=True).first()
            if not comment:
                raise ModerationError(
                    message="Comment not found.",
                    code=ErrorCode.COMMENT_NOT_FOUND,
                )

        report = Report.objects.create(
            reporter_id=reporter_id,
            post_id=post_id,
            comment_id=comment_id,
            reason=reason,
            description=description,
            status=ReportStatus.PENDING,
        )

        logger.info(
            "content_reported",
            extra={
                "reporter_id": reporter_id,
                "report_id": str(report.id),
                "reason": reason,
                "post_id": post_id,
                "comment_id": comment_id,
            },
        )

        return {"report_id": str(report.id), "success": True}

    @staticmethod
    def list_reports(
        status: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> dict:
        """List reports (admin only).

        Args:
            status: Optional status filter.
            cursor: Report ID for pagination.
            limit: Page size.

        Returns:
            Dict with reports, next_cursor, has_more.
        """
        limit = min(limit, 50)

        qs = Report.objects.select_related("reporter", "post", "comment", "reviewed_by").order_by(
            "-created_at"
        )

        if status:
            valid_statuses = [s.value for s in ReportStatus]
            if status in valid_statuses:
                qs = qs.filter(status=status)

        if cursor:
            try:
                cursor_report = Report.objects.filter(id=cursor).values("created_at").first()
                if cursor_report:
                    qs = qs.filter(created_at__lt=cursor_report["created_at"])
            except Exception:  # noqa: S110
                pass

        reports = list(qs[: limit + 1])
        has_more = len(reports) > limit
        reports = reports[:limit]

        report_dtos = []
        for r in reports:
            report_dtos.append(
                {
                    "id": str(r.id),
                    "reporter_id": str(r.reporter_id),
                    "post_id": str(r.post_id) if r.post_id else None,
                    "comment_id": str(r.comment_id) if r.comment_id else None,
                    "reason": r.reason,
                    "description": r.description,
                    "status": r.status,
                    "reviewed_by": (str(r.reviewed_by_id) if r.reviewed_by_id else None),
                    "reviewed_at": (r.reviewed_at.isoformat() if r.reviewed_at else None),
                    "created_at": r.created_at.isoformat(),
                }
            )

        return {
            "reports": report_dtos,
            "next_cursor": str(reports[-1].id) if has_more and reports else None,
            "has_more": has_more,
        }

    @staticmethod
    def review_report(
        report_id: str,
        reviewer_id: str,
        status: str,
    ) -> dict:
        """Review a report (admin only).

        Args:
            report_id: UUID of the report.
            reviewer_id: UUID of the admin reviewer.
            status: New status (reviewed, actioned, dismissed).

        Returns:
            Dict with success status.

        Raises:
            ModerationError: If report not found or invalid status.
        """
        valid_statuses = [
            ReportStatus.REVIEWED,
            ReportStatus.ACTIONED,
            ReportStatus.DISMISSED,
        ]
        if status not in [s.value for s in valid_statuses]:
            raise ModerationError(
                message=f"Invalid status. Must be one of: {', '.join(s.value for s in valid_statuses)}.",
                code=ErrorCode.VALIDATION_ERROR,
            )

        report = Report.objects.filter(id=report_id).first()
        if not report:
            raise ModerationError(
                message="Report not found.",
                code=ErrorCode.REPORT_NOT_FOUND,
            )

        report.status = status
        report.reviewed_by_id = reviewer_id
        report.reviewed_at = datetime.now(timezone.utc)
        report.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"])

        logger.info(
            "report_reviewed",
            extra={
                "report_id": report_id,
                "reviewer_id": reviewer_id,
                "status": status,
            },
        )

        return {"success": True, "report_id": report_id, "status": status}

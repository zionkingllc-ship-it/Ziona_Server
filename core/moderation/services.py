"""
Report/moderation service — business logic for content reporting.

Handles user-submitted content reports and admin report review.
"""

import logging
from datetime import datetime, timezone

from django.db import IntegrityError

from core.engagement.hidden_content import hide_comment_for_user, hide_post_for_user
from core.moderation.models import ModerationActionChoice, Report, ReportReason, ReportStatus
from core.shared.decorators import rate_limit
from core.shared.exceptions import ErrorCode, ModerationError

logger = logging.getLogger("core.moderation")

# Number of reports against the same target before it is auto-hidden.
_AUTO_HIDE_THRESHOLD = 3


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

        # Determine the generic target fields so we can enforce the
        # UniqueConstraint gracefully and run the auto-hide check.
        target_type = "post" if post_id else "comment"
        target_id = post_id if post_id else comment_id

        try:
            report = Report.objects.create(
                reporter_id=reporter_id,
                post_id=post_id,
                comment_id=comment_id,
                target_type=target_type,
                target_id=target_id,
                reason=reason,
                description=description,
                status=ReportStatus.PENDING,
            )
        except IntegrityError:
            # unique_user_report constraint fired — this user already reported
            # this content for the same reason.  Return the existing report id
            # so the caller gets a clean response without creating a duplicate.
            _hide_reported_content_for_reporter(
                reporter_id=reporter_id,
                post_id=post_id,
                comment_id=comment_id,
            )
            existing = Report.objects.filter(
                reporter_id=reporter_id,
                target_type=target_type,
                target_id=target_id,
                reason=reason,
            ).first()
            logger.info(
                "duplicate_report_prevented",
                extra={
                    "reporter_id": reporter_id,
                    "target_type": target_type,
                    "target_id": str(target_id),
                    "reason": reason,
                },
            )
            return {"report_id": str(existing.id) if existing else "", "success": True}

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

        _hide_reported_content_for_reporter(
            reporter_id=reporter_id,
            post_id=post_id,
            comment_id=comment_id,
        )

        # ── Issue #1: Auto-hide content that has reached the report threshold ──
        _apply_auto_hide(post_id=post_id, comment_id=comment_id)

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
            "-created_at", "-id"
        )

        if status:
            valid_statuses = [s.value for s in ReportStatus]
            if status in valid_statuses:
                qs = qs.filter(status=status)

        if cursor:
            try:
                from django.db.models import Q

                cursor_report = Report.objects.filter(id=cursor).values("created_at", "id").first()
                if cursor_report:
                    qs = qs.filter(
                        Q(created_at__lt=cursor_report["created_at"])
                        | Q(
                            created_at=cursor_report["created_at"],
                            id__lt=cursor_report["id"],
                        )
                    )
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
        action: str | None = None,
        internal_notes: str = "",
    ) -> dict:
        """Review a report (admin only).

        Args:
            report_id: UUID of the report.
            reviewer_id: UUID of the admin reviewer.
            status: New status (reviewed, actioned, dismissed).
            action: Optional moderation action to execute on the content
                    (hide_content, warn_user, delete_content, delete_and_warn,
                    dismiss).  When supplied the corresponding side-effect is
                    applied to the reported post or comment.
            internal_notes: Admin-only free-text notes stored on the report.

        Returns:
            Dict with success status.

        Raises:
            ModerationError: If report not found or invalid status/action.
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

        if action is not None:
            valid_actions = [a.value for a in ModerationActionChoice]
            if action not in valid_actions:
                raise ModerationError(
                    message=f"Invalid action. Must be one of: {', '.join(valid_actions)}.",
                    code=ErrorCode.VALIDATION_ERROR,
                )

        report = Report.objects.select_related("post", "comment").filter(id=report_id).first()
        if not report:
            raise ModerationError(
                message="Report not found.",
                code=ErrorCode.REPORT_NOT_FOUND,
            )

        # ── Issue #3: Execute the real side-effect on the reported content ──
        if action and action != ModerationActionChoice.DISMISS:
            _execute_report_action(report=report, action=action)

        update_fields = ["status", "reviewed_by", "reviewed_at", "updated_at"]

        report.status = status
        report.reviewed_by_id = reviewer_id
        report.reviewed_at = datetime.now(timezone.utc)

        if action is not None:
            report.action = action
            update_fields.append("action")

        if internal_notes:
            report.internal_notes = internal_notes
            update_fields.append("internal_notes")

        report.save(update_fields=update_fields)

        logger.info(
            "report_reviewed",
            extra={
                "report_id": report_id,
                "reviewer_id": reviewer_id,
                "status": status,
                "action": action,
            },
        )

        return {"success": True, "report_id": report_id, "status": status}


# ─────────────────────────────────────────
# Module-level helpers
# ─────────────────────────────────────────


def _apply_auto_hide(post_id: str | None, comment_id: str | None) -> None:
    """Auto-hide content that has accumulated enough reports.

    Called immediately after a new report is persisted.  Uses a raw
    .update() call to avoid a second round-trip and stay race-safe.
    """
    from datetime import datetime
    from datetime import timezone as tz

    from core.engagement.models import Comment
    from core.posts.models import Post

    now = datetime.now(tz.utc)

    if post_id:
        report_count = Report.objects.filter(post_id=post_id).count()
        if report_count >= _AUTO_HIDE_THRESHOLD:
            # Soft-delete (hide) the post using the existing deleted_at pattern
            updated = Post.all_objects.filter(id=post_id, deleted_at__isnull=True).update(
                deleted_at=now
            )
            if updated:
                logger.warning(
                    "post_auto_hidden",
                    extra={"post_id": str(post_id), "report_count": report_count},
                )

    elif comment_id:
        report_count = Report.objects.filter(comment_id=comment_id).count()
        if report_count >= _AUTO_HIDE_THRESHOLD:
            updated = Comment.all_objects.filter(id=comment_id, deleted_at__isnull=True).update(
                deleted_at=now
            )
            if updated:
                logger.warning(
                    "comment_auto_hidden",
                    extra={"comment_id": str(comment_id), "report_count": report_count},
                )


def _hide_reported_content_for_reporter(
    *, reporter_id: str, post_id: str | None = None, comment_id: str | None = None
) -> None:
    """Immediately suppress newly reported content for the reporting user only."""
    if post_id:
        hide_post_for_user(reporter_id, str(post_id))
    elif comment_id:
        hide_comment_for_user(reporter_id, str(comment_id))


def _execute_report_action(report: Report, action: str) -> None:
    """Apply the real-world side-effect of a moderation action.

    Runs inside the same database transaction as the report save.
    Mirrors the logic in AdminModerationService but accessible from
    the lighter-weight public ReportService.
    """
    from datetime import datetime
    from datetime import timezone as tz

    from core.engagement.models import Comment
    from core.posts.models import Post

    now = datetime.now(tz.utc)

    if action in (
        ModerationActionChoice.HIDE_CONTENT,
        ModerationActionChoice.DELETE_CONTENT,
        ModerationActionChoice.DELETE_AND_WARN,
    ):
        hard_delete = action in (
            ModerationActionChoice.DELETE_CONTENT,
            ModerationActionChoice.DELETE_AND_WARN,
        )
        if report.post_id:
            if hard_delete:
                Post.all_objects.filter(id=report.post_id).update(deleted_at=now)
            else:
                Post.all_objects.filter(id=report.post_id, deleted_at__isnull=True).update(
                    deleted_at=now
                )
            logger.info(
                "report_action_post", extra={"post_id": str(report.post_id), "action": action}
            )

        elif report.comment_id:
            if hard_delete:
                Comment.all_objects.filter(id=report.comment_id).update(deleted_at=now)
            else:
                Comment.all_objects.filter(id=report.comment_id, deleted_at__isnull=True).update(
                    deleted_at=now
                )
            logger.info(
                "report_action_comment",
                extra={"comment_id": str(report.comment_id), "action": action},
            )

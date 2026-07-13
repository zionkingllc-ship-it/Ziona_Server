"""
Admin Moderation service — enhanced report management with actions.

Atomic mutations with race condition protection on report review.
"""

import logging
from datetime import datetime, timezone

from django.db import transaction
from django.db.models import Count, Q

from core.admin_dashboard.permissions import log_admin_action
from core.shared.exceptions import AdminError, ErrorCode

logger = logging.getLogger("core.admin_dashboard")


class AdminModerationService:
    """Service for admin report listing, review, and moderation actions."""

    @staticmethod
    def list_reports(
        status_filter: str = "",
        search: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """List reports with search, filter, and pagination.

        Uses select_related to eager-load reporter, post, comment, reviewed_by.
        Annotates with content preview data to avoid N+1.

        Returns:
            Dict with reports, total_count, page info, and summary counts.
        """
        from core.moderation.models import Report, ReportStatus

        page_size = min(page_size, 50)
        offset = (page - 1) * page_size

        qs = (
            Report.objects.select_related(
                "reporter",
                "post",
                "post__user",  # Eliminates N+1: _report_to_dict reads post.user.username
                "comment",
                "comment__user",  # Eliminates N+1: _report_to_dict reads comment.user.username
                "comment__post",
                "comment__post__user",
                "reviewed_by",
            )
            .prefetch_related("post__post_media", "comment__post__post_media")
            .order_by("-created_at")
        )

        if status_filter:
            qs = qs.filter(status=status_filter)

        if search:
            qs = qs.filter(
                Q(reporter__username__icontains=search)
                | Q(reporter__email__icontains=search)
                | Q(reason__icontains=search)
                | Q(description__icontains=search)
            )

        total_count = qs.count()
        reports = list(qs[offset : offset + page_size])

        # Summary counts
        summary = Report.objects.aggregate(
            total=Count("id"),
            pending=Count("id", filter=Q(status=ReportStatus.PENDING)),
            reviewed=Count("id", filter=Q(status=ReportStatus.REVIEWED)),
            actioned=Count("id", filter=Q(status=ReportStatus.ACTIONED)),
            dismissed=Count("id", filter=Q(status=ReportStatus.DISMISSED)),
        )

        return {
            "reports": [_report_to_dict(r) for r in reports],
            "total_count": total_count,
            "page": page,
            "page_size": page_size,
            "total_pages": max(1, (total_count + page_size - 1) // page_size),
            "summary": summary,
        }

    @staticmethod
    @transaction.atomic
    def review_report(
        report_id: str,
        action: str,
        reason: str = "",
        internal_notes: str = "",
        admin_user=None,
        ip_address: str = "",
    ) -> dict:
        """Review a report and execute the chosen moderation action.

        Race condition protected: checks status == PENDING inside select_for_update.
        If two admins review simultaneously, the second one gets REPORT_ALREADY_REVIEWED.

        Actions:
            - dismiss: Mark as dismissed, no side effects.
            - hide_content: Soft-delete the reported content.
            - warn_user: Warn the content owner.
            - delete_content: Hard-delete the reported content.
            - delete_and_warn: Delete content + warn user.

        Returns:
            Dict with success status and updated report.

        Raises:
            AdminError: If report not found, already reviewed, or invalid action.
        """
        from core.moderation.models import ModerationActionChoice, Report, ReportStatus

        # Validate action. RESTORE_CONTENT is intentionally excluded here: it is not
        # a pending-report decision but a reversal of an already-actioned report,
        # handled by the dedicated restore_content() method below.
        valid_actions = [
            a.value for a in ModerationActionChoice if a != ModerationActionChoice.RESTORE_CONTENT
        ]
        if action not in valid_actions:
            raise AdminError(
                message=f"Invalid action. Must be one of: {', '.join(valid_actions)}.",
                code=ErrorCode.VALIDATION_ERROR,
            )

        report = (
            Report.objects.select_for_update(of=("self",))
            .filter(id=report_id)
            .select_related("reporter", "post", "comment")
            .first()
        )

        if not report:
            raise AdminError(message="Report not found.", code=ErrorCode.REPORT_NOT_FOUND)

        if report.status != ReportStatus.PENDING:
            raise AdminError(
                message="Report has already been reviewed.",
                code=ErrorCode.REPORT_ALREADY_REVIEWED,
            )

        # Execute the action
        _execute_moderation_action(report, action, reason, admin_user, ip_address)

        # Update report
        new_status = ReportStatus.DISMISSED if action == "dismiss" else ReportStatus.ACTIONED

        report.status = new_status
        report.reviewed_by = admin_user
        report.reviewed_at = datetime.now(timezone.utc)
        report.action = action
        report.internal_notes = internal_notes
        report.save(
            update_fields=[
                "status",
                "reviewed_by",
                "reviewed_at",
                "action",
                "internal_notes",
                "updated_at",
            ]
        )

        log_admin_action(
            admin_user=admin_user,
            action="REPORT_REVIEWED",
            target_type="Report",
            target_id=str(report.id),
            details={
                "action": action,
                "reason": reason,
                "target_type": report.target_type,
                "target_id": str(report.target_id) if report.target_id else None,
            },
            ip_address=ip_address,
        )

        logger.info(
            "report_reviewed",
            extra={
                "report_id": report_id,
                "action": action,
                "admin_id": str(admin_user.id),
            },
        )

        return {"success": True, "report": _report_to_dict(report)}

    @staticmethod
    @transaction.atomic
    def restore_content(report_id: str, admin_user, ip_address: str = "") -> dict:
        """Restore (un-hide) content that was previously hidden via moderation.

        Reverses a ``hide_content`` action by clearing the target's ``deleted_at``.
        Unlike review_report this accepts a report in any state, because a restore
        follows an already-actioned report. Hard-deleted content cannot be
        restored (its FK was nulled on delete), so there is nothing to bring back.

        Raises:
            AdminError: If the report is not found or has no hidden content to restore.
        """
        from core.moderation.models import ModerationActionChoice, Report, ReportStatus

        report = Report.objects.select_for_update(of=("self",)).filter(id=report_id).first()

        if not report:
            raise AdminError(message="Report not found.", code=ErrorCode.REPORT_NOT_FOUND)

        if not _restore_content(report):
            raise AdminError(
                message=(
                    "No hidden content to restore for this report. It may have been "
                    "permanently deleted or was never hidden."
                ),
                code=ErrorCode.VALIDATION_ERROR,
            )

        report.status = ReportStatus.DISMISSED
        report.action = ModerationActionChoice.RESTORE_CONTENT
        report.reviewed_by = admin_user
        report.reviewed_at = datetime.now(timezone.utc)
        report.save(update_fields=["status", "action", "reviewed_by", "reviewed_at", "updated_at"])

        log_admin_action(
            admin_user=admin_user,
            action="CONTENT_RESTORED",
            target_type="Report",
            target_id=str(report.id),
            details={
                "target_type": report.target_type,
                "target_id": str(report.target_id) if report.target_id else None,
            },
            ip_address=ip_address,
        )

        logger.info(
            "content_restored",
            extra={"report_id": report_id, "admin_id": str(admin_user.id)},
        )

        return {"success": True, "report": _report_to_dict(report)}


# ─────────────────────────────────────────
# Private helpers
# ─────────────────────────────────────────


def _execute_moderation_action(report, action: str, reason: str, admin_user, ip_address: str):
    """Execute the side effects of a moderation action.

    This runs inside the same atomic transaction as the report update.
    """
    if action == "dismiss":
        return  # No side effects

    if action in ("hide_content", "delete_content"):
        _moderate_content(report, hard_delete=(action == "delete_content"))

    elif action == "warn_user":
        _warn_content_owner(report, reason, admin_user, ip_address)

    elif action == "delete_and_warn":
        _moderate_content(report, hard_delete=True)
        _warn_content_owner(report, reason, admin_user, ip_address)


def _moderate_content(report, hard_delete: bool = False):
    """Soft-delete or hard-delete the reported content."""
    now = datetime.now(timezone.utc)

    if report.post:
        if hard_delete:
            report.post.delete()
        else:
            report.post.deleted_at = now
            report.post.save(update_fields=["deleted_at", "updated_at"])
        logger.info("moderated_post", extra={"post_id": str(report.post_id)})

    elif report.comment:
        if hard_delete:
            report.comment.delete()
        else:
            report.comment.deleted_at = now
            report.comment.save(update_fields=["deleted_at", "updated_at"])
        logger.info("moderated_comment", extra={"comment_id": str(report.comment_id)})


def _restore_content(report) -> bool:
    """Clear ``deleted_at`` on the report's soft-deleted post/comment.

    Uses ``all_objects`` so the currently-hidden row is visible to the query.
    Returns True if a hidden row was restored, False if there was nothing to
    restore (not hidden, or hard-deleted so the FK id is null).
    """
    from core.engagement.models import Comment
    from core.posts.models import Post

    if report.post_id:
        restored = Post.all_objects.filter(id=report.post_id, deleted_at__isnull=False).update(
            deleted_at=None
        )
        if restored:
            logger.info("restored_post", extra={"post_id": str(report.post_id)})
            return True
    elif report.comment_id:
        restored = Comment.all_objects.filter(
            id=report.comment_id, deleted_at__isnull=False
        ).update(deleted_at=None)
        if restored:
            logger.info("restored_comment", extra={"comment_id": str(report.comment_id)})
            return True
    return False


def _warn_content_owner(report, reason: str, admin_user, ip_address: str):
    """Warn the owner of the reported content."""
    from core.admin_dashboard.user_services import UserManagementService

    owner_id = None
    if report.post:
        owner_id = str(report.post.user_id)
    elif report.comment:
        owner_id = str(report.comment.user_id)

    if owner_id:
        try:
            UserManagementService.warn_user(
                user_id=owner_id,
                reason=reason or "Content violation reported",
                admin_user=admin_user,
                ip_address=ip_address,
            )
        except AdminError:
            # User might already be warned — that's acceptable
            logger.info("User already warned, skipping", extra={"user_id": owner_id})


def _report_to_dict(report) -> dict:
    """Convert Report model to admin-facing dict."""
    reporter_info = {}
    if report.reporter:
        reporter_info = {
            "id": str(report.reporter.id),
            "username": report.reporter.username,
            "avatar_url": report.reporter.avatar_url or "",
        }

    content_preview = ""
    content_owner = ""
    preview_post = None
    if report.post:
        preview_post = report.post
        content_preview = (report.post.caption or "")[:200]
        if hasattr(report.post, "user") and report.post.user:
            content_owner = report.post.user.username
    elif report.comment:
        preview_post = getattr(report.comment, "post", None)
        content_preview = (report.comment.text or "")[:200]
        if hasattr(report.comment, "user") and report.comment.user:
            content_owner = report.comment.user.username

    content_media = _report_media_preview(preview_post)
    first_media = content_media[0] if content_media else {}

    reviewed_by_name = ""
    if report.reviewed_by:
        reviewed_by_name = report.reviewed_by.full_name or report.reviewed_by.username

    return {
        "id": str(report.id),
        "reporter": reporter_info,
        "target_type": report.target_type,
        "target_id": str(report.target_id) if report.target_id else None,
        "post_id": str(report.post_id) if report.post_id else None,
        "comment_id": str(report.comment_id) if report.comment_id else None,
        "reason": report.reason,
        "description": report.description or "",
        "status": report.status,
        "action": report.action or "",
        "internal_notes": report.internal_notes or "",
        "content_preview": content_preview,
        "content_owner": content_owner,
        "content_media_url": first_media.get("url", ""),
        "content_media_type": first_media.get("media_type", ""),
        "content_thumbnail_url": first_media.get("thumbnail_url", ""),
        "content_media": content_media,
        "reviewed_by_name": reviewed_by_name,
        "reviewed_at": report.reviewed_at.isoformat() if report.reviewed_at else None,
        "created_at": report.created_at.isoformat(),
    }


def _report_media_preview(post) -> list[dict]:
    """Return ordered media previews for a reported post/comment context."""
    if not post:
        return []

    media_items = []
    for media in post.post_media.all():
        thumbnail_url = media.thumbnail_url or ""
        if media.media_type == "image" and not thumbnail_url:
            thumbnail_url = media.media_url
        media_items.append(
            {
                "url": media.media_url,
                "media_type": media.media_type or post.post_type,
                "thumbnail_url": thumbnail_url,
                "order": media.order,
            }
        )

    return media_items

"""
Anchor Management service — admin anchor creation, scheduling, and lifecycle.

Uses Celery for deferred posting. All scheduling is idempotent via celery_task_id tracking.
select_for_update + status checks prevent double-posting race conditions.
"""

import logging
from datetime import datetime, timedelta, timezone

from django.db import transaction

from core.admin_dashboard.permissions import log_admin_action
from core.shared.exceptions import AdminError, ErrorCode

logger = logging.getLogger("core.admin_dashboard")

ANCHOR_DURATION_HOURS = 24


class AnchorManagementService:
    """Service for admin anchor CRUD, scheduling, and lifecycle management."""

    @staticmethod
    def list_anchors(
        circle_id: str,
        status_filter: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """List anchors for a circle with optional status filter.

        Uses select_related to avoid N+1 on created_by.
        """
        from core.circles.models import Anchor

        page_size = min(page_size, 50)
        offset = (page - 1) * page_size

        qs = Anchor.objects.filter(circle_id=circle_id, deleted_at__isnull=True).select_related(
            "created_by", "circle"
        )

        if status_filter:
            qs = qs.filter(anchor_status=status_filter)

        total_count = qs.count()
        anchors = list(qs.order_by("-created_at")[offset : offset + page_size])

        return {
            "anchors": [_anchor_to_dict(a) for a in anchors],
            "total_count": total_count,
            "page": page,
            "page_size": page_size,
            "total_pages": max(1, (total_count + page_size - 1) // page_size),
        }

    @staticmethod
    @transaction.atomic
    def create_anchor(
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
        style_data: dict | None = None,
        admin_user=None,
        ip_address: str = "",
    ) -> dict:
        """Create an anchor in DRAFT status.

        Validates content by type:
        - TEXT/devotional: requires title + content
        - MEDIA (image/video): requires at least one media URL
        - BIBLE (bible_verse): requires scripture fields

        Returns:
            Dict with the created anchor data.
        """
        from core.circles.models import Anchor, Circle

        circle = Circle.objects.filter(id=circle_id, deleted_at__isnull=True).first()
        if not circle:
            raise AdminError(message="Circle not found.", code=ErrorCode.CIRCLE_NOT_FOUND)

        # Validate by type
        valid_types = ["bible_verse", "devotional", "text", "image", "video", "image_text"]
        if anchor_type not in valid_types:
            raise AdminError(
                message=f"Invalid anchor type. Must be one of: {', '.join(valid_types)}.",
                code=ErrorCode.VALIDATION_ERROR,
            )

        if anchor_type == "bible_verse" and not scripture_book:
            raise AdminError(
                message="Scripture book is required for Bible anchor type.",
                code=ErrorCode.VALIDATION_ERROR,
            )

        media_fields = _normalise_media_fields(
            anchor_type=anchor_type,
            media_url=media_url,
            anchor_image=anchor_image,
            anchor_video=anchor_video,
            anchor_thumbnail=anchor_thumbnail,
        )

        if anchor_type in ("image", "video") and not (
            media_fields["media_url"]
            or media_fields["anchor_image"]
            or media_fields["anchor_video"]
            or media_fields["anchor_thumbnail"]
        ):
            raise AdminError(
                message="At least one media URL is required for media anchor types.",
                code=ErrorCode.VALIDATION_ERROR,
            )

        # Use a far-future placeholder for published_at/expires_at on drafts
        # These get overwritten when the anchor is actually posted
        placeholder_time = datetime.now(timezone.utc)

        anchor = Anchor.objects.create(
            circle=circle,
            created_by=admin_user,
            anchor_type=anchor_type,
            title=title,
            content=content,
            scripture_book=scripture_book,
            scripture_chapter=scripture_chapter,
            scripture_verse_start=scripture_verse_start,
            scripture_verse_end=scripture_verse_end,
            scripture_translation=scripture_translation,
            scripture_text=scripture_text,
            media_url=media_fields["media_url"],
            anchor_image=media_fields["anchor_image"],
            anchor_video=media_fields["anchor_video"],
            anchor_thumbnail=media_fields["anchor_thumbnail"],
            style_data=style_data or {},
            anchor_status="draft",
            published_at=placeholder_time,
            expires_at=placeholder_time + timedelta(hours=ANCHOR_DURATION_HOURS),
        )

        log_admin_action(
            admin_user=admin_user,
            action="ANCHOR_CREATED",
            target_type="Anchor",
            target_id=str(anchor.id),
            details={"circle_id": circle_id, "anchor_type": anchor_type, "title": title},
            ip_address=ip_address,
        )

        logger.info(
            "anchor_created",
            extra={
                "anchor_id": str(anchor.id),
                "circle_id": circle_id,
                "admin_id": str(admin_user.id),
            },
        )

        return _anchor_to_dict(anchor)

    @staticmethod
    @transaction.atomic
    def schedule_anchor(
        anchor_id: str,
        scheduled_for: datetime,
        admin_user=None,
        ip_address: str = "",
    ) -> dict:
        """Schedule a draft anchor for future posting.

        Idempotent: if already scheduled, revokes old Celery task and reschedules.

        Uses celery_task_id field to track and revoke previous schedules.

        Raises:
            AdminError: If anchor not found, already posted, or scheduled_for is in the past.
        """
        from core.circles.models import Anchor

        now = datetime.now(timezone.utc)

        if scheduled_for <= now:
            raise AdminError(
                message="Scheduled time must be in the future.",
                code=ErrorCode.ANCHOR_SCHEDULE_PAST_DATE,
            )

        anchor = (
            Anchor.objects.select_for_update(of=("self",))
            .filter(id=anchor_id, deleted_at__isnull=True)
            .first()
        )

        if not anchor:
            raise AdminError(message="Anchor not found.", code=ErrorCode.ANCHOR_NOT_FOUND)

        if anchor.anchor_status == "posted":
            raise AdminError(
                message="Anchor is already posted.",
                code=ErrorCode.ANCHOR_ALREADY_POSTED,
            )

        # Revoke old Celery task if rescheduling
        if anchor.celery_task_id:
            _revoke_celery_task(anchor.celery_task_id)

        # Schedule new Celery task
        from core.admin_dashboard.tasks import post_scheduled_anchor

        task = post_scheduled_anchor.apply_async(
            args=[str(anchor.id)],
            eta=scheduled_for,
        )

        anchor.anchor_status = "scheduled"
        anchor.scheduled_for = scheduled_for
        anchor.celery_task_id = task.id
        anchor.save(
            update_fields=[
                "anchor_status",
                "scheduled_for",
                "celery_task_id",
                "updated_at",
            ]
        )

        log_admin_action(
            admin_user=admin_user,
            action="ANCHOR_SCHEDULED",
            target_type="Anchor",
            target_id=str(anchor.id),
            details={
                "scheduled_for": scheduled_for.isoformat(),
                "celery_task_id": task.id,
            },
            ip_address=ip_address,
        )

        logger.info(
            "anchor_scheduled",
            extra={
                "anchor_id": anchor_id,
                "scheduled_for": scheduled_for.isoformat(),
            },
        )

        return _anchor_to_dict(anchor)

    @staticmethod
    @transaction.atomic
    def send_now(anchor_id: str, admin_user=None, ip_address: str = "") -> dict:
        """Immediately post a draft or scheduled anchor.

        Sets status=POSTED, published_at=now, expires_at=now+24h.
        Triggers notifications to circle members.

        Idempotent: if already posted, raises ANCHOR_ALREADY_POSTED.
        """
        from core.circles.models import Anchor

        anchor = (
            Anchor.objects.select_for_update(of=("self",))
            .filter(id=anchor_id, deleted_at__isnull=True)
            .first()
        )

        if not anchor:
            raise AdminError(message="Anchor not found.", code=ErrorCode.ANCHOR_NOT_FOUND)

        if anchor.anchor_status == "posted":
            raise AdminError(
                message="Anchor is already posted.",
                code=ErrorCode.ANCHOR_ALREADY_POSTED,
            )

        # Revoke scheduled Celery task if exists
        if anchor.celery_task_id:
            _revoke_celery_task(anchor.celery_task_id)

        now = datetime.now(timezone.utc)
        anchor.anchor_status = "posted"
        anchor.posted_at = now
        anchor.published_at = now
        anchor.expires_at = now + timedelta(hours=ANCHOR_DURATION_HOURS)
        anchor.celery_task_id = ""
        anchor.save(
            update_fields=[
                "anchor_status",
                "posted_at",
                "published_at",
                "expires_at",
                "celery_task_id",
                "updated_at",
            ]
        )

        log_admin_action(
            admin_user=admin_user,
            action="ANCHOR_POSTED",
            target_type="Anchor",
            target_id=str(anchor.id),
            details={"circle_id": str(anchor.circle_id)},
            ip_address=ip_address,
        )

        # Schedule expiry task
        from core.admin_dashboard.tasks import expire_anchor

        expire_anchor.apply_async(
            args=[str(anchor.id)],
            eta=anchor.expires_at,
        )

        # Trigger notifications asynchronously
        _notify_circle_members(anchor)

        logger.info("anchor_posted_now", extra={"anchor_id": anchor_id})

        return _anchor_to_dict(anchor)

    @staticmethod
    @transaction.atomic
    def edit_scheduled_anchor(
        anchor_id: str,
        admin_user=None,
        ip_address: str = "",
        **updates,
    ) -> dict:
        """Edit a scheduled (not yet posted) anchor's content.

        Raises:
            AdminError: If anchor is not in SCHEDULED status.
        """
        from core.circles.models import Anchor

        anchor = (
            Anchor.objects.select_for_update(of=("self",))
            .filter(id=anchor_id, deleted_at__isnull=True)
            .first()
        )

        if not anchor:
            raise AdminError(message="Anchor not found.", code=ErrorCode.ANCHOR_NOT_FOUND)

        if anchor.anchor_status != "scheduled":
            raise AdminError(
                message="Only scheduled anchors can be edited.",
                code=ErrorCode.ANCHOR_NOT_SCHEDULED,
            )

        allowed_fields = {
            "title",
            "content",
            "scripture_book",
            "scripture_chapter",
            "scripture_verse_start",
            "scripture_verse_end",
            "scripture_translation",
            "scripture_text",
            "media_url",
            "anchor_image",
            "anchor_video",
            "anchor_thumbnail",
            "style_data",
        }
        update_fields = ["updated_at"]

        for field, value in updates.items():
            if field in allowed_fields and value is not None:
                setattr(anchor, field, value)
                update_fields.append(field)

        media_keys = {"media_url", "anchor_image", "anchor_video", "anchor_thumbnail"}
        if media_keys & {field for field, value in updates.items() if value is not None}:
            media_fields = _normalise_media_fields(
                anchor_type=anchor.anchor_type,
                media_url=updates.get("media_url", anchor.media_url),
                anchor_image=updates.get("anchor_image", anchor.anchor_image),
                anchor_video=updates.get("anchor_video", anchor.anchor_video),
                anchor_thumbnail=updates.get("anchor_thumbnail", anchor.anchor_thumbnail),
            )
            for field, value in media_fields.items():
                setattr(anchor, field, value)
                if field not in update_fields:
                    update_fields.append(field)

        anchor.save(update_fields=update_fields)

        log_admin_action(
            admin_user=admin_user,
            action="ANCHOR_EDITED",
            target_type="Anchor",
            target_id=str(anchor.id),
            details={"updated_fields": [f for f in update_fields if f != "updated_at"]},
            ip_address=ip_address,
        )

        return _anchor_to_dict(anchor)

    @staticmethod
    @transaction.atomic
    def cancel_scheduled_anchor(
        anchor_id: str,
        admin_user=None,
        ip_address: str = "",
    ) -> dict:
        """Cancel a scheduled anchor. Revokes the Celery task.

        Idempotent: if already cancelled, returns success.
        """
        from core.circles.models import Anchor

        anchor = (
            Anchor.objects.select_for_update(of=("self",))
            .filter(id=anchor_id, deleted_at__isnull=True)
            .first()
        )

        if not anchor:
            raise AdminError(message="Anchor not found.", code=ErrorCode.ANCHOR_NOT_FOUND)

        if anchor.anchor_status not in ("scheduled", "draft"):
            raise AdminError(
                message="Only scheduled or draft anchors can be cancelled.",
                code=ErrorCode.ANCHOR_ALREADY_POSTED,
            )

        # Revoke Celery task
        if anchor.celery_task_id:
            _revoke_celery_task(anchor.celery_task_id)

        anchor.anchor_status = "cancelled"
        anchor.celery_task_id = ""
        anchor.save(update_fields=["anchor_status", "celery_task_id", "updated_at"])

        log_admin_action(
            admin_user=admin_user,
            action="ANCHOR_CANCELLED",
            target_type="Anchor",
            target_id=str(anchor.id),
            ip_address=ip_address,
        )

        logger.info("anchor_cancelled", extra={"anchor_id": anchor_id})

        return _anchor_to_dict(anchor)


# ─────────────────────────────────────────
# Private helpers
# ─────────────────────────────────────────


def _revoke_celery_task(task_id: str):
    """Revoke a Celery task by ID. Safe to call even if task already ran."""
    try:
        from config.celery import app

        app.control.revoke(task_id, terminate=False)
        logger.info("celery_task_revoked", extra={"task_id": task_id})
    except Exception:
        logger.warning("Failed to revoke Celery task", extra={"task_id": task_id}, exc_info=True)


def _typed_media_fields(anchor_type: str, media_url: str) -> dict[str, str]:
    """Mirror generic media_url into mobile's typed media fields."""
    if anchor_type in ("image", "image_text") and media_url:
        return {"anchor_image": media_url, "anchor_video": ""}
    if anchor_type == "video" and media_url:
        return {"anchor_image": "", "anchor_video": media_url}
    return {"anchor_image": "", "anchor_video": ""}


def _normalise_media_fields(
    *,
    anchor_type: str,
    media_url: str = "",
    anchor_image: str = "",
    anchor_video: str = "",
    anchor_thumbnail: str = "",
) -> dict[str, str]:
    """Resolve legacy mediaUrl and typed media fields without losing either asset.

    `media_url` is kept for older clients that only understand one URL. The typed
    fields are canonical for new admin/mobile rendering and may contain both an
    image and a video on the same anchor.
    """
    media_url = (media_url or "").strip()
    anchor_image = (anchor_image or "").strip()
    anchor_video = (anchor_video or "").strip()
    anchor_thumbnail = (anchor_thumbnail or "").strip()

    if media_url:
        legacy_fields = _typed_media_fields(anchor_type, media_url)
        anchor_image = anchor_image or legacy_fields["anchor_image"]
        anchor_video = anchor_video or legacy_fields["anchor_video"]

    if not media_url:
        if anchor_type == "video":
            media_url = anchor_video or anchor_image or anchor_thumbnail
        else:
            media_url = anchor_image or anchor_video or anchor_thumbnail

    return {
        "media_url": media_url,
        "anchor_image": anchor_image,
        "anchor_video": anchor_video,
        "anchor_thumbnail": anchor_thumbnail,
    }


def _notify_circle_members(anchor):
    """Send push notifications to all circle members about a new anchor.

    Uses CircleMembership — every row in that table represents an active member
    (leaving a circle hard-deletes the row), so no is_active filter is needed.
    """
    try:
        from core.circles.models import CircleMembership
        from core.notifications.services import create_notification

        member_ids = CircleMembership.objects.filter(
            circle_id=anchor.circle_id,
        ).values_list("user_id", flat=True)

        for user_id in member_ids:
            try:
                create_notification(
                    user_id=user_id,
                    type_str="new_anchor",
                    reference_id=anchor.id,
                    reference_type="Anchor",
                    message=f"New anchor in {anchor.circle.name}: {anchor.title}",
                )
            except Exception:
                logger.warning(
                    "Failed to notify member",
                    extra={"user_id": str(user_id), "anchor_id": str(anchor.id)},
                )
    except Exception:
        logger.warning("Failed to notify circle members", exc_info=True)


def _anchor_to_dict(anchor) -> dict:
    """Convert Anchor model to admin-facing dict."""
    author_name = ""
    if anchor.created_by:
        author_name = anchor.created_by.full_name or anchor.created_by.username

    return {
        "id": str(anchor.id),
        "circle_id": str(anchor.circle_id),
        "title": anchor.title,
        "content": anchor.content or "",
        "anchor_type": anchor.anchor_type,
        "anchor_status": anchor.anchor_status,
        "media_url": anchor.media_url or "",
        "anchor_image": anchor.anchor_image or "",
        "anchor_video": anchor.anchor_video or "",
        "anchor_thumbnail": anchor.anchor_thumbnail or "",
        "preview_url": anchor.preview_url or None,
        "scripture_book": anchor.scripture_book or "",
        "scripture_chapter": anchor.scripture_chapter,
        "scripture_verse_start": anchor.scripture_verse_start,
        "scripture_verse_end": anchor.scripture_verse_end,
        "scripture_translation": anchor.scripture_translation or "",
        "scripture_text": anchor.scripture_text or "",
        "style_data": anchor.style_data or {},
        "scheduled_for": anchor.scheduled_for.isoformat() if anchor.scheduled_for else None,
        "posted_at": anchor.posted_at.isoformat() if anchor.posted_at else None,
        "published_at": anchor.published_at.isoformat() if anchor.published_at else None,
        "expires_at": anchor.expires_at.isoformat() if anchor.expires_at else None,
        "author_name": author_name,
        "created_at": anchor.created_at.isoformat() if anchor.created_at else "",
    }

"""
Celery Background Tasks for Circles / Anchors.

Scheduled tasks:
- expire_old_anchors          → every 5 min  — invalidates Redis cache on expiry
- publish_scheduled_anchors   → every 5 min  — fires push notifications on publish
- batched_reaction_notifications → hourly    — batches Amen/Encouraged notifications
- purge_expired_anchors        → nightly 02:00 UTC — hard-deletes anchors > 5 days old
"""
import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from core.circles.anchor_services import invalidate_active_anchor_cache
from core.circles.models import Anchor

logger = logging.getLogger("core.circles")


@shared_task
def expire_old_anchors():
    """
    Run every 5 minutes via Celery Beat.
    Invalidates active anchor cache for circles whose anchor just expired.
    """
    now = timezone.now()
    five_min_ago = now - timedelta(minutes=5)

    recently_expired = (
        Anchor.objects.filter(
            expires_at__gt=five_min_ago,
            expires_at__lte=now,
            deleted_at__isnull=True,
        )
        .values_list("circle_id", flat=True)
        .distinct()
    )

    for circle_id in recently_expired:
        invalidate_active_anchor_cache(str(circle_id))

    return f"Processed {len(recently_expired)} expired anchors"


@shared_task
def publish_scheduled_anchors():
    """
    Run every 5 minutes via Celery Beat.
    Finds anchors whose published_at <= NOW() but haven't been notified yet,
    marks them as notified and triggers push notifications.
    """
    now = timezone.now()

    newly_active = Anchor.objects.filter(
        published_at__lte=now,
        is_notified=False,
        deleted_at__isnull=True,
    ).select_related("circle")

    count = 0
    from core.circles.notification_services import send_new_anchor_notification

    for anchor in newly_active:
        anchor.is_notified = True
        anchor.save(update_fields=["is_notified", "updated_at"])
        invalidate_active_anchor_cache(str(anchor.circle_id))
        send_new_anchor_notification(str(anchor.id))
        count += 1

    return f"Published {count} scheduled anchors"


@shared_task(name="circles.process_batched_reaction_notifications")
def batched_reaction_notifications():
    """
    Run hourly to send batched "Amen" / "Encouraged" notifications.
    """
    from core.circles.notification_services import process_batched_reaction_notifications

    process_batched_reaction_notifications()


@shared_task(name="circles.purge_expired_anchors")
def purge_expired_anchors():
    """
    Run nightly at 02:00 UTC via Celery Beat.

    Hard-deletes any Anchor whose expires_at is more than 5 days in the past.
    After 5 days the mobile app will no longer display past anchors, so keeping
    them is unnecessary database bloat.

    Business rule: anchor lives for 24 h (active) + up to 4 more days in the
    past-anchor history list = 5 days total before permanent removal.
    """
    cutoff = timezone.now() - timedelta(days=5)
    deleted_count, _ = Anchor.objects.filter(expires_at__lt=cutoff).delete()
    logger.info("purge_expired_anchors", extra={"deleted": deleted_count})
    return f"Purged {deleted_count} expired anchors older than 5 days"

"""
Phase 2: Celery Background Tasks for Anchors
- expire_old_anchors: Runs every 5 minutes to invalidate caches
- publish_scheduled_anchors: Checks for anchors that should now be active
"""
from celery import shared_task
from django.utils import timezone

from core.circles.anchor_services import invalidate_active_anchor_cache
from core.circles.models import Anchor


@shared_task
def expire_old_anchors():
    """
    Run every 5 minutes via Celery Beat.
    Invalidates active anchor cache for circles whose anchor just expired.
    """
    now = timezone.now()
    five_min_ago = now - timezone.timedelta(minutes=5)

    # Find anchors that expired in the last 5 minutes
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

        # Dispatch notification to members
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

"""
Celery Background Tasks for Circles / Anchors.

Scheduled tasks:
- expire_old_anchors          → every 5 min  — invalidates Redis cache on expiry
- purge_expired_anchors        → nightly 02:00 UTC — hard-deletes anchors > 5 days old

Publishing a scheduled anchor and notifying its circle lives in
core.admin_dashboard.tasks (post_scheduled_anchor on an ETA, with
check_scheduled_anchors as the every-minute safety net).
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

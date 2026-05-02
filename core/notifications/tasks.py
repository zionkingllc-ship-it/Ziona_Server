import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from core.circles.models import Anchor
from core.notifications.models import Notification, NotificationStatus, NotificationType
from core.notifications.services import create_notification

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60, soft_time_limit=120)
def send_daily_anchor_notifications(self):
    """
    Daily task to batch all new anchors from the last 24 hours
    and send a single notification to each relevant circle member.

    Retries up to 3 times with exponential back-off (60 s, 120 s, 240 s)
    if the database or FCM is unavailable.
    """
    try:
        now = timezone.now()
        yesterday = now - timedelta(days=1)

        # Fetch anchors published in the last 24 hours that haven't been notified yet
        # Optimize to prevent N+1 queries when looping memberships
        anchors = (
            Anchor.objects.filter(
                published_at__gte=yesterday,
                published_at__lte=now,
                is_notified=False,
                deleted_at__isnull=True,
            )
            .select_related("circle")
            .prefetch_related("circle__memberships__user")
        )

        if not anchors.exists():
            logger.info("No new anchors to notify about.")
            return

        user_circle_map = {}
        anchor_ids = []

        for anchor in anchors:
            circle = anchor.circle
            if not circle:
                continue

            anchor_ids.append(anchor.id)

            for membership in circle.memberships.all():
                user = membership.user
                uid = user.id
                if uid not in user_circle_map:
                    user_circle_map[uid] = set()
                user_circle_map[uid].add(circle.name)

        # Mark anchors as notified efficiently
        if anchor_ids:
            Anchor.objects.filter(id__in=anchor_ids).update(
                is_notified=True, updated_at=timezone.now()
            )

        # Create batched notifications
        for uid, circle_names in user_circle_map.items():
            if len(circle_names) == 1:
                circles_str = f"'{list(circle_names)[0]}'"
            else:
                circles_str = f"{len(circle_names)} circles"

            message = f"New anchor posts in {circles_str}"

            try:
                create_notification(
                    user_id=uid,
                    type_str=NotificationType.NEW_ANCHOR,
                    reference_id=None,
                    reference_type="circle_batch",
                    message=message,
                )
            except Exception as e:
                logger.error(
                    f"Failed to create daily anchor notification for user {uid}: {e}", exc_info=True
                )

        logger.info(f"Sent daily anchor notifications to {len(user_circle_map)} users.")

    except Exception as exc:
        logger.error(
            f"send_daily_anchor_notifications failed (attempt {self.request.retries + 1}): {exc}",
            exc_info=True,
        )
        # Exponential back-off: 60s, 120s, 240s
        raise self.retry(exc=exc, countdown=60 * (2**self.request.retries)) from exc


@shared_task(bind=True, max_retries=3, default_retry_delay=60, soft_time_limit=60)
def cleanup_old_notifications(self):
    """Delete soft-deleted notifications older than 90 days."""
    try:
        cutoff = timezone.now() - timedelta(days=90)
        deleted_count, _ = Notification.objects.filter(
            status=NotificationStatus.DELETED, created_at__lt=cutoff
        ).delete()
        logger.info(f"Cleaned up {deleted_count} old deleted notifications.")
    except Exception as exc:
        logger.error(
            f"cleanup_old_notifications failed (attempt {self.request.retries + 1}): {exc}",
            exc_info=True,
        )
        raise self.retry(exc=exc, countdown=60 * (2**self.request.retries)) from exc

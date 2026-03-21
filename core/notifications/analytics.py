"""Analytics tracking for notifications."""

import logging

from django.db.models import F
from django.utils import timezone

from core.notifications.models import NotificationMetrics

logger = logging.getLogger(__name__)


def track_notification_sent(notification_type: str):
    """Increment sent_count for today."""
    try:
        today = timezone.localdate()
        metrics, _ = NotificationMetrics.objects.get_or_create(
            date=today, notification_type=notification_type
        )
        metrics.sent_count = F("sent_count") + 1
        metrics.save(update_fields=["sent_count"])
    except Exception as e:
        logger.error(f"Failed to track sent notification: {e}", exc_info=True)


def track_notification_opened(notification_type: str):
    """Increment opened_count for today."""
    try:
        today = timezone.localdate()
        metrics, _ = NotificationMetrics.objects.get_or_create(
            date=today, notification_type=notification_type
        )
        metrics.opened_count = F("opened_count") + 1
        metrics.save(update_fields=["opened_count"])
    except Exception as e:
        logger.error(f"Failed to track opened notification: {e}", exc_info=True)


def track_user_return(user_id: int):
    """If user opens app within 24h of notification, increment return count."""
    try:
        from datetime import timedelta

        from core.notifications.models import Notification

        # Check if user had a notification in the last 24h
        cutoff = timezone.now() - timedelta(hours=24)
        recent_notif = (
            Notification.objects.filter(user_id=user_id, created_at__gte=cutoff)
            .order_by("-created_at")
            .first()
        )

        if recent_notif:
            today = timezone.localdate()
            metrics, _ = NotificationMetrics.objects.get_or_create(
                date=today, notification_type=recent_notif.notification_type
            )
            metrics.user_return_count = F("user_return_count") + 1
            metrics.save(update_fields=["user_return_count"])
    except Exception as e:
        logger.error(f"Failed to track user return: {e}", exc_info=True)

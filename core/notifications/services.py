import logging
import uuid
from datetime import timedelta
from typing import Any

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from core.notifications.analytics import track_notification_opened, track_notification_sent
from core.notifications.constants import BATCHED_LIKE_TEMPLATES, NOTIFICATION_TEMPLATES, ErrorCodes
from core.notifications.firebase import send_fcm_notification
from core.notifications.models import (
    DeviceToken,
    Notification,
    NotificationPreference,
    NotificationStatus,
    NotificationType,
)

logger = logging.getLogger(__name__)
User = get_user_model()


def _is_notification_enabled(user_id: int, notification_type: str) -> bool:
    """Check if the user has enabled this specific notification type."""
    pref, _ = NotificationPreference.objects.get_or_create(user_id=user_id)

    mapping = {
        NotificationType.NEW_ANCHOR: pref.anchor_notifications,
        NotificationType.REPLY_COMMENT: pref.reply_notifications,
        NotificationType.REPLY_POST: pref.reply_notifications,
        NotificationType.LIKE_POST: pref.like_notifications,
        NotificationType.LIKE_COMMENT: pref.like_notifications,
        NotificationType.NEW_CIRCLE_POST: pref.circle_activity_notifications,
        NotificationType.ADMIN_ANNOUNCEMENT: pref.admin_announcements,
    }
    # Mentions are always enabled if not explicitly mapped above
    return mapping.get(notification_type, True)


def create_notification(
    user_id: int,
    type_str: str,
    reference_id: uuid.UUID,
    reference_type: str,
    message: str,
) -> Notification | None:
    """
    Create an in-app notification and trigger a push notification.
    Applies preferences and anti-spam rules.
    """
    if not _is_notification_enabled(user_id, type_str):
        return None

    # Anti-spam: Do not recreate exact same notification within 1 hour
    one_hour_ago = timezone.now() - timedelta(hours=1)
    is_duplicate = Notification.objects.filter(
        user_id=user_id,
        notification_type=type_str,
        reference_id=reference_id,
        reference_type=reference_type,
        created_at__gte=one_hour_ago,
    ).exists()

    if is_duplicate:
        logger.info(f"Duplicate notification prevented for {user_id} ({type_str})")
        return None

    notification = Notification.objects.create(
        user_id=user_id,
        notification_type=type_str,
        reference_id=reference_id,
        reference_type=reference_type,
        message=message,
    )

    # Trigger push notification asynchronously (would be a Celery task in prod)
    # Stubbed here for direct service call
    send_push_notification(
        user_id=user_id,
        title="Ziona App",
        body=message,
        data={
            "type": type_str,
            "reference_id": str(reference_id) if reference_id else "",
            "reference_type": reference_type,
            "screen": "NotificationDetail",  # Example screen
        },
    )

    return notification


def send_push_notification(user_id: int, title: str, body: str, data: dict[str, Any]) -> None:
    """Send push notification to all active device tokens for the user."""
    tokens = DeviceToken.objects.filter(user_id=user_id, is_active=True).values_list(
        "token", flat=True
    )
    if not tokens:
        return

    # Call FCM (Firebase Cloud Messaging) integration
    send_fcm_notification(list(tokens), title, body, data)

    # Track analytics
    if data and "type" in data:
        track_notification_sent(data["type"])


def mark_as_read(notification_id: uuid.UUID, user_id: int) -> bool:
    """Mark a notification as read and track the open event."""
    try:
        notification = Notification.objects.get(
            id=notification_id, user_id=user_id, status=NotificationStatus.ACTIVE
        )
        if not notification.is_read:
            notification.is_read = True
            notification.save(update_fields=["is_read", "updated_at"])
            track_notification_opened(notification.notification_type)
        return True
    except Notification.DoesNotExist as err:
        raise ValueError(ErrorCodes.NOTIFICATION_NOT_FOUND) from err


def get_notifications(user_id: int, limit: int = 20, cursor: str | None = None):
    """
    Fetch paginated notifications.
    Unread first, then order by created_at DESC.
    """
    queryset = Notification.objects.filter(
        user_id=user_id, status=NotificationStatus.ACTIVE
    ).order_fields("is_read", "-created_at")

    if cursor:
        # Simple cursor implementation using created_at ISO string
        try:
            from django.utils.dateparse import parse_datetime

            cursor_date = parse_datetime(cursor)
            if cursor_date:
                queryset = queryset.filter(created_at__lt=cursor_date)
        except Exception as err:
            logger.warning(f"Error parsing cursor in notifications: {err}")

    return queryset[:limit]


def get_unread_count(user_id: int) -> int:
    """Get count of unread notifications for a user."""
    return Notification.objects.filter(
        user_id=user_id, is_read=False, status=NotificationStatus.ACTIVE
    ).count()


def update_preferences(user_id: int, preferences_dict: dict[str, bool]) -> NotificationPreference:
    """Update user notification preferences."""
    pref, _ = NotificationPreference.objects.get_or_create(user_id=user_id)

    for key, value in preferences_dict.items():
        if hasattr(pref, key):
            setattr(pref, key, value)

    pref.save()
    return pref


def register_device_token(user_id: int, token: str, platform: str) -> str:
    """Register a new device token, enforcing a 5 device limit."""
    with transaction.atomic():
        # Enforce max 5 devices rule before creating
        active_tokens = DeviceToken.objects.filter(user_id=user_id)
        if active_tokens.count() >= 5 and not active_tokens.filter(token=token).exists():
            inactive_tokens = active_tokens.filter(is_active=False).order_by("created_at")
            if inactive_tokens.exists():
                inactive_tokens.first().delete()
            else:
                # All slots taken by active tokens, replace oldest
                oldest_token = active_tokens.order_by("created_at").first()
                if oldest_token:
                    oldest_token.delete()

        # Now create or update the token
        obj, created = DeviceToken.objects.update_or_create(
            user_id=user_id, token=token, defaults={"platform": platform, "is_active": True}
        )
        return "Success"


def batch_like_notifications(
    actor_username: str,
    recipient_id: int,
    reference_id: uuid.UUID,
    reference_type: str,
    like_type: str,
):
    """
    Track and batch multiple likes within a 5-minute window.

    Uses atomic Redis SET operations (sadd / scard / smembers) to avoid the
    read-modify-write race condition present in a plain list-based cache approach.
    Two concurrent likes both call sadd independently; each is a single atomic
    server-side op so neither can overwrite the other.

    Falls back to the original list approach when the cache backend does not
    expose a Redis client (e.g. LocMemCache in tests / CI).
    """
    cache_key = f"likes_batch_{reference_type}_{reference_id}"

    try:
        # Atomic Redis SET path ─ preferred in production
        redis_client = cache.client.get_client()
        # sadd returns the number of elements actually added (0 if already present)
        redis_client.sadd(cache_key, actor_username)
        # Refresh the TTL on every new like so the window stays at 5 minutes
        redis_client.expire(cache_key, 300)
        count = redis_client.scard(cache_key)
        members = {
            m.decode() if isinstance(m, bytes) else m for m in redis_client.smembers(cache_key)
        }
        first_liker = next(iter(members))  # deterministic enough for display
    except (AttributeError, Exception):
        # Fallback: non-Redis cache backend (tests, dev with LocMemCache)
        likes_data = cache.get(cache_key, [])
        if actor_username not in likes_data:
            likes_data.append(actor_username)
            cache.set(cache_key, likes_data, timeout=300)
        count = len(likes_data)
        first_liker = likes_data[0]

    if count == 1:
        message = NOTIFICATION_TEMPLATES[like_type].format(username=actor_username)
    else:
        others_count = count - 1
        message = BATCHED_LIKE_TEMPLATES[like_type].format(
            username=first_liker, others_count=others_count
        )

    # Create or update existing unread notification
    existing_notif = (
        Notification.objects.filter(
            user_id=recipient_id,
            notification_type=like_type,
            reference_id=reference_id,
            reference_type=reference_type,
            is_read=False,
        )
        .order_by("-created_at")
        .first()
    )

    if existing_notif:
        existing_notif.message = message
        existing_notif.save(update_fields=["message", "updated_at"])
    else:
        create_notification(
            user_id=recipient_id,
            type_str=like_type,
            reference_id=reference_id,
            reference_type=reference_type,
            message=message,
        )


def create_admin_announcement(admin_id: int, message: str, target_users: list[int] | None = None):
    """
    Create announcements for all users or a targeted list.
    """
    # Verify admin logic here if necessary, though assumed checked by caller
    if target_users is None:
        target_users = list(User.objects.values_list("id", flat=True))

    announcements = []
    formatted_msg = NOTIFICATION_TEMPLATES["admin_announcement"].format(message=message)

    for uid in target_users:
        announcements.append(
            Notification(
                user_id=uid,
                notification_type=NotificationType.ADMIN_ANNOUNCEMENT,
                message=formatted_msg,
            )
        )

    with transaction.atomic():
        Notification.objects.bulk_create(announcements, batch_size=1000)

    # In production, dispatch async celery task to handle FCM pushing
    pass

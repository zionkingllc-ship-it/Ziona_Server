import logging
import re
import uuid
from datetime import timedelta
from typing import Any

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction
from django.db.models.functions import Lower
from django.utils import timezone

from core.notifications.analytics import track_notification_opened, track_notification_sent
from core.notifications.constants import BATCHED_LIKE_TEMPLATES, NOTIFICATION_TEMPLATES, ErrorCodes
from core.notifications.firebase import get_fcm_project_id, send_fcm_debug, send_fcm_notification
from core.notifications.models import (
    DeviceToken,
    Notification,
    NotificationPreference,
    NotificationStatus,
    NotificationType,
)

logger = logging.getLogger(__name__)
User = get_user_model()
MENTION_REGEX = re.compile(r"(?<![\w.])@([A-Za-z0-9_]{3,30})\b")

MENTION_CONTEXT_LABELS = {
    "post": "a post",
    "comment": "a comment",
    "circle_post": "a circle post",
    "circle_post_comment": "a circle comment",
    "anchor_response": "an anchor response",
}


def _is_notification_enabled(user_id: int, notification_type: str) -> bool:
    """Check whether the 12-field mobile preference contract permits delivery."""
    pref, _ = NotificationPreference.objects.get_or_create(user_id=user_id)

    mapping = {
        NotificationType.NEW_ANCHOR: pref.circle_anchor_post,
        NotificationType.REPLY_COMMENT: pref.in_app_comment and pref.interaction_comment,
        NotificationType.REPLY_POST: pref.in_app_comment and pref.interaction_post_interaction,
        NotificationType.LIKE_POST: pref.in_app_likes and pref.interaction_likes,
        NotificationType.LIKE_COMMENT: pref.in_app_likes and pref.interaction_likes,
        NotificationType.MENTION: pref.in_app_mention_and_tags,
        NotificationType.NEW_CIRCLE_POST: pref.circle_anchor_post,
        NotificationType.SUPPORT_REPLY: True,
        # Admin/system announcements do not have a user-facing granular toggle.
        NotificationType.ADMIN_ANNOUNCEMENT: True,
    }
    return mapping.get(notification_type, True)


def create_notification(
    user_id: int,
    type_str: str,
    reference_id: uuid.UUID,
    reference_type: str,
    message: str,
    sender_id: int | None = None,
    title: str = "",
    respect_preferences: bool = True,
    bypass_duplicate_check: bool = False,
    push_data: dict[str, str] | None = None,
) -> Notification | None:
    """
    Create an in-app notification and trigger a push notification.
    Applies preferences and anti-spam rules.

    Args:
        sender_id: The user who triggered the notification (e.g. a liker, commenter).
                   Pass None for system/admin notifications.
    """
    if respect_preferences and not _is_notification_enabled(user_id, type_str):
        return None

    # Anti-spam: Do not recreate exact same notification within 1 hour
    one_hour_ago = timezone.now() - timedelta(hours=1)
    is_duplicate = (
        False
        if bypass_duplicate_check
        else Notification.objects.filter(
            user_id=user_id,
            notification_type=type_str,
            reference_id=reference_id,
            reference_type=reference_type,
            created_at__gte=one_hour_ago,
        ).exists()
    )

    if is_duplicate:
        logger.info(f"Duplicate notification prevented for {user_id} ({type_str})")
        return None

    notification = Notification.objects.create(
        user_id=user_id,
        notification_type=type_str,
        reference_id=reference_id,
        reference_type=reference_type,
        title=title,
        message=message,
        sender_id=sender_id,
    )

    # Trigger push notification asynchronously (would be a Celery task in prod)
    notification_data = {
        "type": type_str,
        "reference_id": str(reference_id) if reference_id else "",
        "reference_type": reference_type,
        "screen": "NotificationDetail",
    }
    if push_data:
        notification_data.update({key: str(value) for key, value in push_data.items()})

    send_push_notification(
        user_id=user_id,
        title=title or "Ziona App",
        body=message,
        data=notification_data,
    )

    return notification


def extract_mentioned_usernames(text: str) -> list[str]:
    """Return unique @username tokens in first-seen order."""
    seen: set[str] = set()
    usernames: list[str] = []

    for username in MENTION_REGEX.findall(text or ""):
        normalized = username.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        usernames.append(username)

    return usernames


def _resolve_mentioned_users(text: str, circle_id: str | None = None):
    """Resolve mention tokens to active users, optionally scoped to a circle."""
    usernames = extract_mentioned_usernames(text)
    if not usernames:
        return []

    normalized_usernames = [username.lower() for username in usernames]
    queryset = (
        User.objects.filter(deleted_at__isnull=True)
        .annotate(username_lower=Lower("username"))
        .filter(username_lower__in=normalized_usernames)
    )
    if circle_id:
        queryset = queryset.filter(circle_memberships__circle_id=circle_id).distinct()

    return list(queryset)


def mentioned_user_ids(text: str, circle_id: str | None = None) -> list[str]:
    """Resolve @mentions to active user IDs for persisted mention metadata."""
    return [str(user.id) for user in _resolve_mentioned_users(text, circle_id=circle_id)]


def notify_mentions(
    *,
    text: str,
    actor,
    reference_id: uuid.UUID,
    reference_type: str,
    circle_id: str | None = None,
) -> list[Notification]:
    """Create mention notifications for users referenced in text.

    Circle-scoped content passes circle_id so only current members can be
    notified, preventing private circle activity from leaking to non-members.
    """
    mentioned_users = _resolve_mentioned_users(text, circle_id=circle_id)
    if not mentioned_users:
        return []

    actor_id = getattr(actor, "id", None)
    actor_username = getattr(actor, "username", None) or getattr(actor, "name", None) or "Someone"
    context_label = MENTION_CONTEXT_LABELS.get(reference_type, "content")
    created_notifications: list[Notification] = []

    for mentioned_user in mentioned_users:
        if actor_id and mentioned_user.id == actor_id:
            continue

        notification = create_notification(
            user_id=mentioned_user.id,
            type_str=NotificationType.MENTION,
            reference_id=reference_id,
            reference_type=reference_type,
            title="You were mentioned",
            message=f"{actor_username} mentioned you in {context_label}",
            sender_id=actor_id,
            push_data={
                "actor_id": str(actor_id or ""),
                "circle_id": str(circle_id or ""),
            },
        )
        if notification:
            created_notifications.append(notification)

    return created_notifications


def send_push_notification(user_id: int, title: str, body: str, data: dict[str, Any]) -> None:
    """Send push notification to all active device tokens for the user."""
    tokens = list(
        DeviceToken.objects.filter(user_id=user_id, is_active=True).values_list("token", flat=True)
    )
    if not tokens:
        logger.info(
            "push_notification_skipped_no_tokens",
            extra={"user_id": str(user_id), "notification_type": (data or {}).get("type")},
        )
        return

    logger.info(
        "push_notification_dispatch_started",
        extra={
            "user_id": str(user_id),
            "token_count": len(tokens),
            "notification_type": (data or {}).get("type"),
            "reference_id": (data or {}).get("reference_id"),
        },
    )

    summary = send_fcm_notification(tokens, title, body, data) or {}
    logger.info(
        "push_notification_dispatch_finished",
        extra={
            "user_id": str(user_id),
            "token_count": len(tokens),
            "success_count": summary.get("success_count", 0),
            "failure_count": summary.get("failure_count", 0),
            "invalid_token_count": summary.get("invalid_token_count", 0),
        },
    )

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
    Uses select_related('sender') so the GraphQL user field costs zero extra queries.
    """
    queryset = (
        Notification.objects.filter(user_id=user_id, status=NotificationStatus.ACTIVE)
        .select_related("sender")
        .order_by("is_read", "-created_at")
    )

    if cursor:
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
    """Register or transfer a device token to the current user.

    Push provider tokens identify a concrete app install, not an account. If a
    tester logs out and another user signs in on the same phone, the same token
    must move to the new user instead of creating a duplicate row. Keeping token
    ownership singular also prevents notifications for one account leaking to a
    previous account on the same device.
    """
    token = token.strip()
    platform = platform.strip().lower()

    if not token:
        raise ValueError("DEVICE_TOKEN_REQUIRED")
    if not platform:
        raise ValueError("DEVICE_PLATFORM_REQUIRED")

    with transaction.atomic():
        token_obj, created = DeviceToken.objects.update_or_create(
            token=token,
            defaults={
                "user_id": user_id,
                "platform": platform,
                "is_active": True,
            },
        )

        _enforce_device_token_limit(user_id=user_id, keep_token=token)

        logger.info(
            "device_token_registered",
            extra={
                "user_id": str(user_id),
                "platform": platform,
                "device_token_id": str(token_obj.id),
                "was_created": created,
                "token_tail": token[-8:],
            },
        )

        return "Success"


def _enforce_device_token_limit(user_id: int, keep_token: str, max_tokens: int = 5) -> None:
    """Keep at most ``max_tokens`` active device tokens for a user."""
    user_tokens = list(
        DeviceToken.objects.select_for_update()
        .filter(user_id=user_id)
        .order_by("is_active", "created_at")
    )
    excess_count = len(user_tokens) - max_tokens
    if excess_count <= 0:
        return

    removable_ids = [token_obj.id for token_obj in user_tokens if token_obj.token != keep_token][
        :excess_count
    ]
    if removable_ids:
        DeviceToken.objects.filter(id__in=removable_ids).delete()


def _classify_token(token: str) -> str:
    """Best-effort guess at a token's type, for push debugging output.

    - ``expo``     → Expo proxy token (ExponentPushToken[...]); FCM cannot deliver.
    - ``apns_raw`` → raw APNs device token (hex); not an FCM registration token.
    - ``fcm_like`` → long opaque string consistent with a real FCM token.
    """
    token = token or ""
    if token.startswith("ExponentPushToken"):
        return "expo"
    if len(token) < 100 and token and all(c in "0123456789abcdefABCDEF" for c in token):
        return "apns_raw"
    return "fcm_like"


def send_debug_push(
    target_user_id: Any,
    title: str = "Ziona test push 🔔",
    body: str = "If you can read this, FCM delivery is working.",
    include_inactive: bool = False,
) -> dict[str, Any]:
    """Send a test push to a user's device tokens and report the raw FCM outcome.

    Diagnostic only and non-destructive (never deactivates tokens), so the same
    token can be retried after a client fix. Returns the Firebase project id the
    backend is wired to plus a per-token accept/reject breakdown.

    TEMPORARY: added to validate the push pipeline end-to-end during mobile push
    setup. Safe to delete once client push delivery is confirmed.
    """
    qs = DeviceToken.objects.filter(user_id=target_user_id).order_by("-created_at")
    if not include_inactive:
        qs = qs.filter(is_active=True)
    rows = list(qs)

    tokens = [row.token for row in rows]
    data = {"type": "debug_push", "screen": "NotificationDetail"}
    outcomes = send_fcm_debug(tokens, title, body, data)  # 1:1 with rows

    results: list[dict[str, Any]] = []
    success_count = 0
    for row, outcome in zip(rows, outcomes, strict=True):
        if outcome["success"]:
            success_count += 1
        tok = row.token or ""
        preview = f"{tok[:12]}…{tok[-6:]}" if len(tok) > 20 else tok
        results.append(
            {
                "token_preview": preview,
                "platform": row.platform,
                "is_active": row.is_active,
                "token_kind": _classify_token(tok),
                "success": outcome["success"],
                "message_id": outcome["message_id"],
                "error_code": outcome["error_code"],
                "error_message": outcome["error_message"],
            }
        )

    logger.info(
        "debug_push_sent",
        extra={
            "target_user_id": str(target_user_id),
            "tokens_tried": len(rows),
            "success_count": success_count,
        },
    )

    return {
        "project_id": get_fcm_project_id(),
        "tokens_tried": len(rows),
        "success_count": success_count,
        "failure_count": len(rows) - success_count,
        "results": results,
    }


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

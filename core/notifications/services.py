import base64
import json
import logging
import re
import uuid
from datetime import timedelta
from typing import Any
from urllib.parse import quote

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction
from django.db.models import BooleanField, Case, Exists, OuterRef, Q, Value, When
from django.db.models.functions import Lower
from django.utils import timezone

from core.notifications.analytics import track_notification_opened, track_notification_sent
from core.notifications.constants import BATCHED_LIKE_TEMPLATES, NOTIFICATION_TEMPLATES, ErrorCodes
from core.notifications.firebase import get_fcm_project_id, send_fcm_debug, send_fcm_notification
from core.notifications.models import (
    DeviceToken,
    Notification,
    NotificationMutedUser,
    NotificationPreference,
    NotificationStatus,
    NotificationType,
)
from core.shared.utils import build_post_share_url, build_profile_share_url, parse_uuid

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

NOTIFICATION_CATEGORY_TYPES = {
    "interactions": {
        NotificationType.REPLY_COMMENT,
        NotificationType.REPLY_POST,
        NotificationType.LIKE_POST,
        NotificationType.LIKE_COMMENT,
        NotificationType.MENTION,
        NotificationType.NEW_FOLLOWER,
    },
    "circles": {
        NotificationType.NEW_ANCHOR,
        NotificationType.NEW_CIRCLE_POST,
    },
    "updates": {
        NotificationType.ADMIN_ANNOUNCEMENT,
        NotificationType.SUPPORT_REPLY,
    },
}

CIRCLE_REFERENCE_TYPES = {"circle_post", "circle_post_comment", "anchor", "anchor_response"}

# Canonical reference_type vocabulary is lowercase snake_case — it is what both
# clients switch on and what CIRCLE_REFERENCE_TYPES matches. Some writers used
# CamelCase model names ("Anchor", "ContactMessage"), which silently missed both
# the mobile route map (JS lookups are case-sensitive) and the Circles filter.
_REFERENCE_TYPE_ALIASES = {
    "profile": "user",
    "contactmessage": "contact_message",
}


def _normalize_reference_type(reference_type: str | None) -> str:
    """Canonicalise a reference type to the lowercase snake_case vocabulary."""
    value = (reference_type or "").strip().lower()
    return _REFERENCE_TYPE_ALIASES.get(value, value)


# Token kinds FCM cannot deliver to (see _classify_token). Rejected at
# registration so the client learns immediately, instead of the token being
# stored, rejected by FCM, and silently deactivated forever.
_UNDELIVERABLE_TOKEN_KINDS = {"expo", "apns_raw"}


def _normalize_notification_category(category: str | None) -> str | None:
    if not category:
        return None
    raw_value = getattr(category, "value", category)
    value = str(raw_value).strip().lower()
    if value in {"", "all"}:
        return None
    return value


def _is_sender_muted(user_id: int, sender_id: int | str | None) -> bool:
    """Return whether this user muted the actor who triggered a notification."""
    if not sender_id:
        return False
    if str(user_id) == str(sender_id):
        return False
    return NotificationMutedUser.objects.filter(user_id=user_id, muted_user_id=sender_id).exists()


def _is_notification_enabled(
    user_id: int,
    notification_type: str,
    reference_type: str = "",
) -> bool:
    """Check whether the 12-field mobile preference contract permits delivery."""
    pref, _ = NotificationPreference.objects.get_or_create(user_id=user_id)
    ref_type = (reference_type or "").strip().lower()

    # anchor_response is circle content: reactions and replies on a reflection
    # must answer to the circle toggles, not the global comment/like ones.
    if notification_type == NotificationType.LIKE_POST and ref_type == "circle_post":
        return pref.circle_likes
    if notification_type == NotificationType.LIKE_COMMENT and ref_type in {
        "circle_post_comment",
        "anchor_response",
    }:
        return pref.circle_likes
    if notification_type in {NotificationType.REPLY_POST, NotificationType.REPLY_COMMENT} and (
        ref_type in {"circle_post", "circle_post_comment", "anchor_response"}
    ):
        return pref.circle_comment

    mapping = {
        NotificationType.NEW_ANCHOR: pref.circle_anchor_post,
        NotificationType.REPLY_COMMENT: pref.in_app_comment and pref.interaction_comment,
        NotificationType.REPLY_POST: pref.in_app_comment and pref.interaction_post_interaction,
        NotificationType.LIKE_POST: pref.in_app_likes and pref.interaction_likes,
        NotificationType.LIKE_COMMENT: pref.in_app_likes and pref.interaction_likes,
        NotificationType.MENTION: pref.in_app_mention_and_tags,
        NotificationType.NEW_CIRCLE_POST: pref.circle_anchor_post,
        NotificationType.NEW_FOLLOWER: pref.in_app_new_followers and pref.interaction_new_follower,
        NotificationType.SUPPORT_REPLY: True,
        # Admin/system announcements do not have a user-facing granular toggle.
        NotificationType.ADMIN_ANNOUNCEMENT: True,
    }
    return mapping.get(notification_type, True)


def _filter_muted_senders(queryset, user_id: int):
    # NOT EXISTS rather than NOT IN (subquery) — CLAUDE.md §23.
    muted = NotificationMutedUser.objects.filter(
        user_id=user_id, muted_user_id=OuterRef("sender_id")
    )
    return queryset.annotate(_sender_muted=Exists(muted)).filter(_sender_muted=False)


def _filter_notification_category(queryset, category: str | None):
    normalized = _normalize_notification_category(category)
    if normalized is None:
        return queryset

    if normalized == "interactions":
        return queryset.filter(notification_type__in=NOTIFICATION_CATEGORY_TYPES["interactions"])
    if normalized == "circles":
        return queryset.filter(
            Q(notification_type__in=NOTIFICATION_CATEGORY_TYPES["circles"])
            | Q(reference_type__in=CIRCLE_REFERENCE_TYPES)
        )
    if normalized == "updates":
        return queryset.filter(notification_type__in=NOTIFICATION_CATEGORY_TYPES["updates"])

    return queryset.none()


def _annotate_sender_viewer_state(queryset, user_id: int):
    from core.follows.models import Follow

    return queryset.annotate(
        sender_is_following=Exists(
            Follow.objects.filter(follower_id=user_id, following_id=OuterRef("sender_id"))
        ),
        sender_is_followed_by=Exists(
            Follow.objects.filter(follower_id=OuterRef("sender_id"), following_id=user_id)
        ),
        sender_is_owner=Case(
            When(sender_id=user_id, then=Value(True)),
            default=Value(False),
            output_field=BooleanField(),
        ),
    )


def queue_push_notification(user_id, title: str, body: str, data: dict[str, Any]) -> None:
    """Queue push delivery after commit instead of blocking the request.

    Deferred with `transaction.on_commit` so a rolled-back transaction never
    sends a push for a notification that no longer exists. If the broker cannot
    be reached the push is sent inline rather than silently dropped — a slow
    request is better than a lost notification.
    """
    payload = {
        "user_id": str(user_id),
        "title": title,
        "body": body,
        "data": data,
    }

    def _dispatch() -> None:
        from core.notifications.tasks import send_push_notification_task

        try:
            send_push_notification_task.apply_async(kwargs=payload)
        except Exception:
            logger.warning(
                "push_notification_enqueue_failed_sending_inline",
                extra={"user_id": str(user_id), "notification_type": data.get("type")},
                exc_info=True,
            )
            send_push_notification(user_id=user_id, title=title, body=body, data=data)

    transaction.on_commit(_dispatch)


def _build_destination_context(pairs) -> dict[tuple[str, str], dict[str, str]]:
    """Resolve the parent ids a destination needs, in one query per reference type.

    ``pairs`` is an iterable of ``(reference_type, reference_id)``. Returns
    ``{(ref_type, ref_id): {"parentId": ..., "circleId": ...}}``.

    Resolving a whole page at once is what keeps the notification list off an
    N+1: four of the reference types need a parent lookup, and the list resolver
    used to run one query per row (two, because ``deepLink`` and ``destination``
    each recomputed it) — 41 queries for a 20-row page.
    """
    from core.circles.models import AnchorResponse, CirclePost, CirclePostComment
    from core.engagement.models import Comment

    ids_by_type: dict[str, set[str]] = {
        "comment": set(),
        "circle_post": set(),
        "circle_post_comment": set(),
        "anchor_response": set(),
        "anchor": set(),
    }
    for ref_type, ref_id in pairs:
        normalized = _normalize_reference_type(ref_type)
        if normalized in ids_by_type and ref_id:
            ids_by_type[normalized].add(str(ref_id))

    context: dict[tuple[str, str], dict[str, str]] = {}

    if ids_by_type["comment"]:
        for row in Comment.objects.filter(
            id__in=ids_by_type["comment"], deleted_at__isnull=True
        ).values("id", "post_id"):
            context[("comment", str(row["id"]))] = {"parentId": str(row["post_id"])}

    if ids_by_type["circle_post"]:
        for row in CirclePost.objects.filter(
            id__in=ids_by_type["circle_post"], deleted_at__isnull=True
        ).values("id", "circle_id"):
            context[("circle_post", str(row["id"]))] = {"circleId": str(row["circle_id"])}

    if ids_by_type["circle_post_comment"]:
        for row in CirclePostComment.objects.filter(
            id__in=ids_by_type["circle_post_comment"], deleted_at__isnull=True
        ).values("id", "post_id", "post__circle_id"):
            context[("circle_post_comment", str(row["id"]))] = {
                "parentId": str(row["post_id"]),
                "circleId": str(row["post__circle_id"]),
            }

    if ids_by_type["anchor_response"]:
        for row in AnchorResponse.objects.filter(
            id__in=ids_by_type["anchor_response"], deleted_at__isnull=True
        ).values("id", "anchor_id", "anchor__circle_id"):
            context[("anchor_response", str(row["id"]))] = {
                "parentId": str(row["anchor_id"]),
                "circleId": str(row["anchor__circle_id"]),
            }

    if ids_by_type["anchor"]:
        # all_objects, not objects: an anchor expires after 24h and may be soft
        # deleted, but the notification must still route the member into the
        # circle it belonged to rather than dead-ending on the list.
        from core.circles.models import Anchor

        for row in Anchor.all_objects.filter(id__in=ids_by_type["anchor"]).values(
            "id", "circle_id"
        ):
            context[("anchor", str(row["id"]))] = {"circleId": str(row["circle_id"])}

    return context


def build_notification_destination(
    notification_type: str,
    reference_type: str,
    reference_id: uuid.UUID | str | None,
    *,
    notification_id: uuid.UUID | str | None = None,
    context: dict[tuple[str, str], dict[str, str]] | None = None,
) -> dict[str, str]:
    """Build mobile navigation metadata from an existing notification reference.

    ``context`` is the prefetched map from :func:`_build_destination_context`.
    When omitted one is built for this single reference, so callers that handle
    one notification (push delivery) keep working unchanged.
    """
    ref_type = _normalize_reference_type(reference_type)
    ref_id = str(reference_id) if reference_id else ""
    fallback_id = str(notification_id) if notification_id else ref_id
    fallback = {
        "route": "notification_detail",
        "entityType": "notification",
        "entityId": fallback_id,
        "secondaryEntityId": "",
        "circleId": "",
        "deepLink": "",
    }
    if not ref_id:
        return fallback

    if context is None:
        context = _build_destination_context([(ref_type, ref_id)])
    resolved = context.get((ref_type, ref_id), {})

    if ref_type == "post":
        return {
            "route": "post_detail",
            "entityType": "post",
            "entityId": ref_id,
            "secondaryEntityId": "",
            "circleId": "",
            "deepLink": build_post_share_url(settings.APP_SHARE_BASE_URL, ref_id),
        }

    if ref_type in {"profile", "user"}:
        return {
            "route": "profile",
            "entityType": "user",
            "entityId": ref_id,
            "secondaryEntityId": "",
            "circleId": "",
            "deepLink": build_profile_share_url(settings.APP_SHARE_BASE_URL, ref_id),
        }

    if ref_type == "comment":
        post_id = resolved.get("parentId", "")
        if not post_id:
            return {**fallback, "entityType": "comment", "entityId": ref_id}
        return {
            "route": "comment_thread",
            "entityType": "comment",
            "entityId": ref_id,
            "secondaryEntityId": post_id,
            "circleId": "",
            "deepLink": (
                f"{build_post_share_url(settings.APP_SHARE_BASE_URL, post_id)}"
                f"?commentId={quote(ref_id, safe='')}"
            ),
        }

    if ref_type == "circle_post":
        circle_id = resolved.get("circleId", "")
        return {
            "route": "circle_post_detail",
            "entityType": "circle_post",
            "entityId": ref_id,
            "secondaryEntityId": circle_id,
            "circleId": circle_id,
            "deepLink": "",
        }

    if ref_type == "circle_post_comment":
        return {
            "route": "circle_post_comment_thread",
            "entityType": "circle_post_comment",
            "entityId": ref_id,
            "secondaryEntityId": resolved.get("parentId", ""),
            "circleId": resolved.get("circleId", ""),
            "deepLink": "",
        }

    if ref_type == "anchor":
        # secondaryEntityId carries the circle id so the client can open the
        # circle the anchor belongs to; an anchor on its own is not a
        # navigable destination once it has expired.
        circle_id = resolved.get("circleId", "")
        return {
            "route": "anchor_detail",
            "entityType": "anchor",
            "entityId": ref_id,
            "secondaryEntityId": circle_id,
            "circleId": circle_id,
            "deepLink": "",
        }

    if ref_type == "anchor_response":
        return {
            "route": "anchor_response",
            "entityType": "anchor_response",
            "entityId": ref_id,
            "secondaryEntityId": resolved.get("parentId", ""),
            "circleId": resolved.get("circleId", ""),
            "deepLink": "",
        }

    if ref_type == "contact_message":
        return {
            "route": "support_ticket",
            "entityType": "contact_message",
            "entityId": ref_id,
            "secondaryEntityId": "",
            "circleId": "",
            "deepLink": "",
        }

    return fallback


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
    # Canonicalise before anything reads it: the stored value drives the mobile
    # route map, the Circles filter and the destination builder, and all three
    # are case-sensitive.
    reference_type = _normalize_reference_type(reference_type)

    if sender_id and _is_sender_muted(user_id, sender_id):
        logger.info(
            "notification_skipped_muted_sender",
            extra={
                "user_id": str(user_id),
                "sender_id": str(sender_id),
                "notification_type": type_str,
            },
        )
        return None

    if respect_preferences and not _is_notification_enabled(user_id, type_str, reference_type):
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

    # Push is queued to Celery (see queue_push_notification) so the FCM
    # round-trip never blocks the request that created this notification.
    # Keys are camelCase to match the app-wide mobile contract (CLAUDE.md §5.1)
    # and the GraphQL notification fields — the mobile tap-handler reads
    # data.referenceType / data.referenceId.
    destination = build_notification_destination(
        notification_type=type_str,
        reference_type=reference_type,
        reference_id=reference_id,
        notification_id=notification.id,
    )
    notification_data = {
        "type": type_str,
        "referenceId": str(reference_id) if reference_id else "",
        "referenceType": reference_type,
        "screen": "NotificationDetail",
        "senderId": str(sender_id) if sender_id else "",
        "destinationRoute": destination["route"],
        "destinationEntityType": destination["entityType"],
        "destinationEntityId": destination["entityId"],
        "destinationSecondaryEntityId": destination["secondaryEntityId"],
        "destinationCircleId": destination["circleId"],
        "deepLink": destination["deepLink"],
    }
    if push_data:
        notification_data.update({key: str(value) for key, value in push_data.items()})

    queue_push_notification(
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
                "actorId": str(actor_id or ""),
                "circleId": str(circle_id or ""),
            },
        )
        if notification:
            created_notifications.append(notification)

    return created_notifications


def send_push_notification(user_id: int, title: str, body: str, data: dict[str, Any]) -> None:
    """Send push notification to all active device tokens for the user."""
    sender_id = (data or {}).get("senderId") or (data or {}).get("actorId")
    if sender_id and _is_sender_muted(user_id, sender_id):
        logger.info(
            "push_notification_skipped_muted_sender",
            extra={
                "user_id": str(user_id),
                "sender_id": str(sender_id),
                "notification_type": (data or {}).get("type"),
            },
        )
        return

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


def get_notifications(
    user_id: int,
    limit: int = 20,
    cursor: str | None = None,
    category: str | None = None,
):
    """
    Fetch paginated notifications.
    Unread first, then order by created_at DESC.
    Uses select_related('sender') so the GraphQL user field costs zero extra queries.
    """
    queryset = (
        Notification.objects.filter(user_id=user_id, status=NotificationStatus.ACTIVE)
        .select_related("sender")
        .order_by("is_read", "-created_at", "-id")
    )
    queryset = _filter_muted_senders(queryset, user_id)
    queryset = _filter_notification_category(queryset, category)
    queryset = _annotate_sender_viewer_state(queryset, user_id)
    queryset = _apply_notification_cursor(queryset, cursor)

    return queryset[:limit]


def encode_notification_cursor(notification) -> str:
    """Encode the full sort key — unread flag, timestamp and id — as an opaque cursor."""
    payload = {
        "v": 1,
        "r": bool(notification.is_read),
        "ts": notification.created_at.isoformat(),
        "id": str(notification.id),
    }
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def _apply_notification_cursor(queryset, cursor: str | None):
    """Keyset pagination matching the (is_read, -created_at, -id) ordering.

    Paginating on ``created_at`` alone was wrong because unread sorts ahead of
    read regardless of age: a user with more than one page of unread ended page
    one on an old unread timestamp, and every *read* notification newer than it
    was then filtered out permanently. Those rows were unreachable.

    Legacy bare-ISO cursors from clients paging mid-deploy fall back to the old
    timestamp filter, which self-heals on the next page.
    """
    if not cursor:
        return queryset

    from django.utils.dateparse import parse_datetime

    try:
        data = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
    except Exception:
        data = None

    if not isinstance(data, dict) or "id" not in data:
        try:
            legacy_date = parse_datetime(cursor)
        except Exception:
            legacy_date = None
        if legacy_date:
            return queryset.filter(created_at__lt=legacy_date)
        logger.warning("Unparseable notification cursor — serving the first page")
        return queryset

    cursor_ts = parse_datetime(data.get("ts") or "")
    if cursor_ts is None:
        return queryset

    is_read = bool(data.get("r"))
    # is_read sorts ascending, so "after the cursor" means unread -> read.
    keyset = Q(is_read__gt=is_read) | Q(is_read=is_read, created_at__lt=cursor_ts)

    cursor_id = parse_uuid(data.get("id"))
    if cursor_id is not None:
        # Only add the id tiebreak for an id the UUIDField can actually accept —
        # a hand-edited cursor would otherwise raise at queryset evaluation,
        # well away from here, and surface as a 500 rather than a first page.
        keyset |= Q(is_read=is_read, created_at=cursor_ts, id__lt=cursor_id)

    return queryset.filter(keyset)


def get_unread_count(user_id: int) -> int:
    """Get count of unread notifications for a user."""
    queryset = Notification.objects.filter(
        user_id=user_id, is_read=False, status=NotificationStatus.ACTIVE
    )
    return _filter_muted_senders(queryset, user_id).count()


def get_muted_user_ids(user_id: int) -> list[str]:
    """Return the user's muted notification senders as stable string IDs."""
    return [
        str(muted_user_id)
        for muted_user_id in NotificationMutedUser.objects.filter(user_id=user_id)
        .order_by("created_at")
        .values_list("muted_user_id", flat=True)
    ]


def update_preferences(
    user_id: int,
    preferences_dict: dict[str, bool],
    muted_user_ids: list[str] | None = None,
) -> NotificationPreference:
    """Update user notification preferences."""
    with transaction.atomic():
        pref, _ = NotificationPreference.objects.select_for_update().get_or_create(user_id=user_id)

        for key, value in preferences_dict.items():
            if hasattr(pref, key):
                setattr(pref, key, value)

        pref.save()

        if muted_user_ids is not None:
            normalized_muted_ids = {
                str(muted_id).strip()
                for muted_id in muted_user_ids
                if str(muted_id).strip() and str(muted_id).strip() != str(user_id)
            }
            valid_muted_ids = set(
                User.objects.filter(id__in=normalized_muted_ids, deleted_at__isnull=True)
                .exclude(id=user_id)
                .values_list("id", flat=True)
            )
            NotificationMutedUser.objects.filter(user_id=user_id).delete()
            NotificationMutedUser.objects.bulk_create(
                [
                    NotificationMutedUser(user_id=user_id, muted_user_id=muted_id)
                    for muted_id in valid_muted_ids
                ],
                ignore_conflicts=True,
            )

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

    # Reject token kinds FCM can never deliver to. Storing them is worse than
    # refusing: FCM rejects them with INVALID_ARGUMENT, which permanently sets
    # is_active=False, and from then on every event silently short-circuits on
    # "no active tokens" — push dies with no error the client ever sees.
    token_kind = _classify_token(token)
    if token_kind in _UNDELIVERABLE_TOKEN_KINDS:
        logger.warning(
            "device_token_rejected_unsupported_kind",
            extra={
                "user_id": str(user_id),
                "platform": platform,
                "token_kind": token_kind,
                "token_tail": token[-8:],
            },
        )
        raise ValueError(ErrorCodes.INVALID_DEVICE_TOKEN)

    with transaction.atomic():
        # Take every row lock this registration could need up front, in one
        # globally consistent order — see _lock_registration_rows. Without
        # this, two concurrent registrations for the same user deadlock:
        # each locks its own token row (update_or_create), then sweeps the
        # user's other rows (limit enforcement) that the other one holds.
        _lock_registration_rows(user_id=user_id, token=token)

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


def _registration_lock_queryset(user_id: int, token: str):
    """Queryset of every row a registration may touch, in global lock order.

    Covers the user's existing tokens (limit enforcement deletes among them)
    plus the incoming token's row even if it currently belongs to another user
    (the transfer case). Ordered by pk so every concurrent registration
    acquires locks in the same sequence — the invariant that makes a lock
    cycle (deadlock) impossible.
    """
    return (
        DeviceToken.objects.select_for_update()
        .filter(Q(user_id=user_id) | Q(token=token))
        .order_by("pk")
    )


def _lock_registration_rows(user_id: int, token: str) -> None:
    """Acquire all row locks for a token registration. Must run in a transaction."""
    # list() forces the SELECT ... FOR UPDATE to execute and take the locks.
    list(_registration_lock_queryset(user_id=user_id, token=token))


def _enforce_device_token_limit(user_id: int, keep_token: str, max_tokens: int = 5) -> None:
    """Keep at most ``max_tokens`` active device tokens for a user.

    Takes no locks of its own: the caller (register_device_token) has already
    locked the user's rows via _lock_registration_rows. Locking again here —
    after update_or_create locked the incoming token's row — is what used to
    deadlock concurrent registrations.
    """
    user_tokens = list(
        DeviceToken.objects.filter(user_id=user_id).order_by("is_active", "created_at")
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
    actor_id: int | None = None,
    template_key: str | None = None,
):
    """
    Track and batch multiple likes within a 5-minute window.

    ``template_key`` selects the wording independently of ``like_type``, so
    content that reuses an existing notification type can still read correctly —
    an Amen on a reflection is a LIKE_COMMENT to the client but is not "liked
    your comment". Defaults to ``like_type``, so existing callers are unchanged.

    Uses atomic Redis SET operations (sadd / scard / smembers) to avoid the
    read-modify-write race condition present in a plain list-based cache approach.
    Two concurrent likes both call sadd independently; each is a single atomic
    server-side op so neither can overwrite the other.

    Falls back to the original list approach when the cache backend does not
    expose a Redis client (e.g. LocMemCache in tests / CI).
    """
    if actor_id and _is_sender_muted(recipient_id, actor_id):
        return

    cache_key = f"likes_batch_{reference_type}_{reference_id}"
    # Keyed on the actor id, not the username: username is nullable (OAuth
    # signups keep it null until they pick one), so two username-less actors
    # used to collapse to a single set member and the recipient was told
    # "None liked your post" no matter how many people had.
    member = f"{actor_id or actor_username}:{actor_username or 'Someone'}"

    def _display(value: str) -> str:
        _, separator, name = value.partition(":")
        return name if separator else value

    try:
        # Atomic Redis SET path ─ preferred in production
        redis_client = cache.client.get_client()
        # sadd returns the number of elements actually added (0 if already present)
        redis_client.sadd(cache_key, member)
        # Refresh the TTL on every new like so the window stays at 5 minutes
        redis_client.expire(cache_key, 300)
        count = redis_client.scard(cache_key)
        members = {
            m.decode() if isinstance(m, bytes) else m for m in redis_client.smembers(cache_key)
        }
        first_liker = _display(next(iter(members)))  # deterministic enough for display
    except (AttributeError, Exception):
        # Fallback: non-Redis cache backend (tests, dev with LocMemCache)
        likes_data = cache.get(cache_key, [])
        if member not in likes_data:
            likes_data.append(member)
            cache.set(cache_key, likes_data, timeout=300)
        count = len(likes_data)
        first_liker = _display(likes_data[0])

    wording = template_key or like_type
    if count == 1:
        message = NOTIFICATION_TEMPLATES[wording].format(username=actor_username)
    else:
        others_count = count - 1
        message = BATCHED_LIKE_TEMPLATES[wording].format(
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
        if actor_id:
            existing_notif.sender_id = actor_id
        existing_notif.save(update_fields=["message", "sender_id", "updated_at"])
    else:
        create_notification(
            user_id=recipient_id,
            type_str=like_type,
            reference_id=reference_id,
            reference_type=reference_type,
            message=message,
            sender_id=actor_id,
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

    title = "Ziona Update"
    data = {
        "type": NotificationType.ADMIN_ANNOUNCEMENT,
        "referenceId": "",
        "referenceType": "",
        "screen": "NotificationDetail",
        "senderId": "",
        "destinationRoute": "notification_detail",
        "destinationEntityType": "notification",
        "destinationEntityId": "",
        "destinationSecondaryEntityId": "",
        "deepLink": "",
    }

    with transaction.atomic():
        Notification.objects.bulk_create(announcements, batch_size=1000)

        def _queue_push_batches() -> None:
            from core.notifications.tasks import send_admin_announcement_push_batch

            batch_size = 500
            for index in range(0, len(target_users), batch_size):
                send_admin_announcement_push_batch.apply_async(
                    kwargs={
                        "user_ids": [str(uid) for uid in target_users[index : index + batch_size]],
                        "title": title,
                        "body": formatted_msg,
                        "data": data,
                    }
                )

        transaction.on_commit(_queue_push_batches)

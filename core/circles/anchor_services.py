"""
Phase 2: Anchor Service Layer
Handles retrieval, creation, caching, and time calculations for circle anchors.
"""

from datetime import timedelta

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from core.circles.models import Anchor, AnchorPage, Circle, CircleMembership
from core.engagement.cache import EngagementCache
from core.engagement.hidden_content import exclude_hidden_circle_content
from core.shared.exceptions import ZionaError

# ──────────────────────────────────────────────
#  Time Remaining Calculation
# ──────────────────────────────────────────────


def calculate_time_remaining(expires_at) -> str:
    """
    Calculate time until expiration formatted as "23h 10m 23s".
    Returns "0h 0m 0s" if already expired.
    """
    now = timezone.now()
    delta = expires_at - now

    if delta.total_seconds() <= 0:
        return "0h 0m 0s"

    total_seconds = int(delta.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return f"{hours}h {minutes}m {seconds}s"


# ──────────────────────────────────────────────
#  Retrieval (with caching)
# ──────────────────────────────────────────────


def get_active_anchor(circle_id: str, viewer_id: str | None = None) -> Anchor | None:
    """
    Get the currently active anchor for a circle.
    Cached for 5 minutes. Invalidated on create/delete/expire.
    """
    if viewer_id and EngagementCache.is_circle_content_hidden(viewer_id, "circle", circle_id):
        return None

    cache_key = f"active_anchor:{circle_id}"
    anchor = cache.get(cache_key)

    if anchor is None:
        now = timezone.now()
        anchor = (
            Anchor.objects.filter(
                circle_id=circle_id,
                published_at__lte=now,
                expires_at__gt=now,
                deleted_at__isnull=True,
            )
            .select_related("created_by", "circle")
            .first()
        )

        if anchor:
            cache.set(cache_key, anchor, timeout=300)  # 5 minutes

    if (
        anchor
        and viewer_id
        and EngagementCache.is_circle_content_hidden(viewer_id, "anchor", anchor.id)
    ):
        return None

    return anchor


def invalidate_active_anchor_cache(circle_id: str) -> None:
    """Remove cached active anchor when data changes."""
    cache.delete(f"active_anchor:{circle_id}")


def get_anchor_by_date(circle_id: str, date, viewer_id: str | None = None) -> Anchor | None:
    """Get anchor that was active on a specific date."""
    if viewer_id and EngagementCache.is_circle_content_hidden(viewer_id, "circle", circle_id):
        return None

    queryset = Anchor.objects.filter(
        circle_id=circle_id,
        published_at__date=date,
        deleted_at__isnull=True,
    ).select_related("created_by", "circle")
    queryset = exclude_hidden_circle_content(queryset, viewer_id, target_type="anchor")
    return queryset.first()


def get_anchor_by_id(anchor_id: str, viewer_id: str | None = None) -> Anchor:
    """
    Fetch a single Anchor by its UUID.

    Does NOT use the Redis cache — this is a direct ID lookup (e.g. deep-link
    from a push notification) where caching adds no value and stale data is
    undesirable. Raises ZionaError if the anchor does not exist or is deleted.
    """
    from core.shared.exceptions import ZionaError

    queryset = Anchor.objects.select_related("created_by", "circle").filter(
        id=anchor_id,
        deleted_at__isnull=True,
    )
    queryset = exclude_hidden_circle_content(queryset, viewer_id, target_type="anchor")
    queryset = exclude_hidden_circle_content(
        queryset,
        viewer_id,
        target_type="circle",
        target_field="circle_id",
    )

    anchor = queryset.first()
    if not anchor:
        raise ZionaError(message="Anchor not found", code="ANCHOR_NOT_FOUND") from None
    return anchor


def get_anchor_history(
    circle_id: str,
    limit: int = 20,
    cursor: str | None = None,
    include_active: bool = True,
    max_age_days: int | None = None,
    viewer_id: str | None = None,
) -> list[Anchor]:
    """Get past anchors for a circle, ordered by published_at DESC.

    Args:
        max_age_days: When set, only anchors whose expires_at is within this
                      many days in the past are returned. This enforces the
                      5-day display window without waiting for the purge task.
    """
    if viewer_id and EngagementCache.is_circle_content_hidden(viewer_id, "circle", circle_id):
        return []

    queryset = (
        Anchor.objects.filter(
            circle_id=circle_id,
            deleted_at__isnull=True,
        )
        .select_related("created_by", "circle")
        .order_by("-published_at")
    )
    queryset = exclude_hidden_circle_content(queryset, viewer_id, target_type="anchor")

    if not include_active:
        queryset = queryset.filter(expires_at__lte=timezone.now())

    if max_age_days is not None:
        cutoff = timezone.now() - timedelta(days=max_age_days)
        queryset = queryset.filter(expires_at__gte=cutoff)

    if cursor:
        queryset = queryset.filter(published_at__lt=cursor)

    return list(queryset[:limit])


# ──────────────────────────────────────────────
#  Creation (with scheduling & overlap prevention)
# ──────────────────────────────────────────────


@transaction.atomic
def create_anchor(
    creator_id: str,
    circle_id: str,
    anchor_type: str,
    title: str,
    content: str = "",
    published_at=None,
    # Scripture fields
    scripture_book: str = "",
    scripture_chapter: int | None = None,
    scripture_verse_start: int | None = None,
    scripture_verse_end: int | None = None,
    scripture_translation: str = "KJV",
    scripture_text: str = "",
    # Media fields
    media_url: str = "",
    anchor_image: str = "",
    anchor_video: str = "",
    anchor_thumbnail: str = "",
    # Visual / theming fields
    background_colors: list[str] | None = None,
    background_image: str = "",
    anchor_text: str = "",
    anchor_verse: str = "",
    anchor_image_text: str = "",
    # Devotional pages
    pages: list | None = None,
) -> Anchor:
    """
    Create a new anchor with 24-hour expiration.
    Validates type, prevents overlapping anchors, and supports scheduling.
    """
    now = timezone.now()

    # ── Validate circle exists ──
    try:
        circle = Circle.objects.get(id=circle_id, is_active=True, deleted_at__isnull=True)
    except Circle.DoesNotExist:
        raise ZionaError(
            message="Circle does not exist or has been deleted", code="CIRCLE_NOT_FOUND"
        ) from None

    # ── Validate permission ──
    try:
        membership = CircleMembership.objects.get(circle_id=circle_id, user_id=creator_id)
        if not membership.is_admin():
            raise ZionaError(
                message="Only Circle admins can create anchors", code="NOT_CIRCLE_ADMIN"
            )
    except CircleMembership.DoesNotExist:
        raise ZionaError(
            message="You do not have permission to create anchors", code="CANNOT_CREATE_ANCHOR"
        ) from None

    # ── Validate anchor type ──
    valid_types = ["bible_verse", "devotional", "text", "image", "video", "image_text"]
    if anchor_type not in valid_types:
        raise ZionaError(
            message="Anchor type must be bible_verse, devotional, text, image, video, or image_text",
            code="INVALID_ANCHOR_TYPE",
        )

    # ── Bible verse requires scripture reference ──
    if anchor_type == "bible_verse" and not scripture_book:
        raise ZionaError(
            message="Bible verse anchors require scripture reference",
            code="MISSING_SCRIPTURE_REFERENCE",
        )

    # ── Handle scheduling ──
    if published_at is None:
        published_at = now
    elif published_at < now:
        raise ZionaError(
            message="Cannot schedule an anchor in the past", code="CANNOT_SCHEDULE_PAST"
        )
    elif published_at > now + timedelta(days=30):
        raise ZionaError(
            message="Cannot schedule an anchor more than 30 days in advance",
            code="SCHEDULE_TOO_FAR",
        )

    expires_at = published_at + timedelta(hours=24)

    # ── Prevent overlapping active anchors ──
    overlapping = Anchor.objects.filter(
        circle_id=circle_id,
        published_at__lt=expires_at,
        expires_at__gt=published_at,
        deleted_at__isnull=True,
    ).exists()

    if overlapping:
        raise ZionaError(
            message="An active anchor already exists for this time window",
            code="OVERLAPPING_ANCHOR",
        )

    if not anchor_image and anchor_type in ("image", "image_text") and media_url:
        anchor_image = media_url

    if not anchor_video and anchor_type == "video" and media_url:
        anchor_video = media_url

    # ── Create anchor ──
    anchor = Anchor.objects.create(
        circle=circle,
        created_by_id=creator_id,
        anchor_type=anchor_type,
        title=title,
        content=content,
        scripture_book=scripture_book,
        scripture_chapter=scripture_chapter,
        scripture_verse_start=scripture_verse_start,
        scripture_verse_end=scripture_verse_end,
        scripture_translation=scripture_translation,
        scripture_text=scripture_text,
        media_url=media_url,
        anchor_image=anchor_image,
        anchor_video=anchor_video,
        anchor_thumbnail=anchor_thumbnail,
        background_colors=background_colors or [],
        background_image=background_image,
        anchor_text=anchor_text,
        anchor_verse=anchor_verse,
        anchor_image_text=anchor_image_text,
        published_at=published_at,
        expires_at=expires_at,
    )

    # ── Create pages for devotional ──
    if anchor_type == "devotional" and pages:
        for idx, page_data in enumerate(pages, start=1):
            AnchorPage.objects.create(
                anchor=anchor,
                page_number=idx,
                title=page_data.get("title", ""),
                content=page_data.get("content", ""),
                media_url=page_data.get("media_url", ""),
            )

    # ── Invalidate cache ──
    invalidate_active_anchor_cache(circle_id)

    return anchor

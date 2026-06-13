import mimetypes
import os
import re
from urllib.parse import urlparse

from django.db import transaction
from django.db.models import Count, Exists, F, OuterRef, Prefetch

from core.circles.models import (
    Anchor,
    AnchorEngagement,
    Circle,
    CircleMembership,
    CirclePost,
    CirclePostEngagement,
)
from core.media.models import MediaFile, MediaStatus
from core.media.models import MediaType as StoredMediaType
from core.shared.exceptions import ZionaError

# Shorthand error codes
CIRCLE_NOT_FOUND = "CIRCLE_NOT_FOUND"
CIRCLE_INACTIVE = "CIRCLE_INACTIVE"
ALREADY_MEMBER = "ALREADY_MEMBER"
NOT_MEMBER = "NOT_MEMBER"


def get_all_circles(
    viewer_id: str | None = None, limit: int = 20, cursor: str | None = None
) -> list[Circle]:
    """
    Get all active Circles with pagination
    OPTIMIZED: Prevent N+1 queries using select_related, annotate, and prefetch_related
    """
    # 1. Base Query: Active circles, not deleted
    queryset = (
        Circle.objects.filter(is_active=True, deleted_at__isnull=True)
        .select_related("created_by")
        .annotate(member_count=Count("memberships"))
        .prefetch_related(
            Prefetch(
                "memberships",
                queryset=CircleMembership.objects.select_related("user").order_by(
                    "role", "joined_at"
                )[:4],
                to_attr="preview_memberships",
            )
        )
        .order_by("-created_at")
    )

    # 2. Pagination (cursor assumes created_at string for now)
    if cursor:
        queryset = queryset.filter(created_at__lt=cursor)

    circles = list(queryset[:limit])

    # 3. Dynamic Subscription check
    if viewer_id:
        # Get all circle IDs the user is subscribed to in one query
        subscribed_ids = set(
            CircleMembership.objects.filter(
                user_id=viewer_id, circle_id__in=[c.id for c in circles]
            ).values_list("circle_id", flat=True)
        )

        for circle in circles:
            # We monkey-patch a temporary attribute to prevent N+1 in the resolver
            circle._is_viewer_subscribed = circle.id in subscribed_ids
    else:
        for circle in circles:
            circle._is_viewer_subscribed = False

    return circles


def get_my_circles(user_id: str, limit: int = 20, cursor: str | None = None) -> list[Circle]:
    """Get Circles current user has joined"""
    queryset = (
        Circle.objects.filter(memberships__user_id=user_id, is_active=True, deleted_at__isnull=True)
        .select_related("created_by")
        .annotate(member_count=Count("memberships"))
        .order_by("-memberships__joined_at")
    )

    if cursor:
        # Assuming cursor is joined_at for my_circles
        queryset = queryset.filter(memberships__joined_at__lt=cursor)

    circles = list(queryset[:limit])
    for circle in circles:
        circle._is_viewer_subscribed = True

    return circles


def get_suggested_circles(user_id: str | None = None, limit: int = 10) -> list[Circle]:
    """Get recommended Circles (simple popular algorithm)"""
    queryset = Circle.objects.filter(is_active=True, deleted_at__isnull=True)

    if user_id:
        queryset = queryset.exclude(memberships__user_id=user_id)

    circles = list(
        queryset.annotate(member_count=Count("memberships")).order_by("-member_count")[:limit]
    )

    for circle in circles:
        circle._is_viewer_subscribed = False

    return circles


@transaction.atomic
def join_circle(user_id: str, circle_id: str) -> CircleMembership:
    """Subscribe user to Circle"""
    try:
        circle = Circle.objects.get(id=circle_id, is_active=True, deleted_at__isnull=True)
    except Circle.DoesNotExist:
        raise ZionaError(
            message="Circle does not exist or has been deleted", code=CIRCLE_NOT_FOUND
        ) from None

    # Check if already a member
    if CircleMembership.objects.filter(circle=circle, user_id=user_id).exists():
        raise ZionaError(message="You are already a member of this Circle", code=ALREADY_MEMBER)

    return CircleMembership.objects.create(circle=circle, user_id=user_id, role="member")


@transaction.atomic
def leave_circle(user_id: str, circle_id: str) -> bool:
    """Unsubscribe user from Circle"""
    try:
        membership = CircleMembership.objects.get(circle_id=circle_id, user_id=user_id)
    except CircleMembership.DoesNotExist:
        raise ZionaError(message="You are not a member of this Circle", code=NOT_MEMBER) from None

    # Prevent last admin from leaving (Basic check)
    if membership.role == "admin":
        admin_count = CircleMembership.objects.filter(circle_id=circle_id, role="admin").count()
        if admin_count <= 1:
            raise ZionaError(
                message="Cannot leave Circle as the only admin", code="CANNOT_LEAVE_LAST_ADMIN"
            )

    membership.delete()
    return True


@transaction.atomic
def create_circle(creator_id: str, name: str, description: str, cover_image: str) -> Circle:
    """Create new Circle (admin only - permissions checked at schema layer)"""
    circle = Circle.objects.create(
        name=name,
        description=description,
        cover_image=cover_image,
        created_by_id=creator_id,
        is_active=True,
    )

    # Creator automatically becomes first admin
    CircleMembership.objects.create(circle=circle, user_id=creator_id, role="admin")

    return circle


# ──────────────────────────────────────────────
#  PHASE 5: Circle Feed & Engagement Services
# ──────────────────────────────────────────────

CIRCLE_POST_NOT_FOUND = "CIRCLE_POST_NOT_FOUND"
ANCHOR_NOT_FOUND = "ANCHOR_NOT_FOUND"
VALIDATION_ERROR = "VALIDATION_ERROR"
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".m4v"}
URL_PATTERN = re.compile(
    r"^(?:http|ftp)s?://"
    r"(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)|"
    r"localhost|"
    r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})"
    r"(?::\d+)?"
    r"(?:/?|[/?]\S+)$",
    re.IGNORECASE,
)


def _normalize_media_type_hint(media_type: str | None) -> str | None:
    if not media_type:
        return None
    value = getattr(media_type, "value", media_type)
    return str(value).strip().lower() or None


def _infer_media_type_from_url(url: str) -> str:
    extension = os.path.splitext(urlparse(url).path)[1].lower()
    if extension in VIDEO_EXTENSIONS:
        return StoredMediaType.VIDEO
    return StoredMediaType.IMAGE


def _infer_file_type(url: str, media_type: str) -> str:
    guessed_type, _ = mimetypes.guess_type(url)
    if guessed_type:
        return guessed_type
    return "video/mp4" if media_type == StoredMediaType.VIDEO else "image/jpeg"


def _resolve_circle_post_media(
    *,
    user_id: str,
    media_ids: list[str] | None = None,
    media_urls: list[str] | None = None,
    media_type: str | None = None,
    thumbnail_url: str | None = None,
    width: int | None = None,
    height: int | None = None,
    duration: int | None = None,
) -> tuple[list[MediaFile], str | None]:
    resolved_media_files: list[MediaFile] = []

    if media_ids:
        media_lookup = {
            str(media.id): media for media in MediaFile.objects.filter(id__in=media_ids)
        }
        if len(media_lookup) != len(set(media_ids)):
            raise ZionaError(
                message="One or more media IDs were not found",
                code=VALIDATION_ERROR,
            )

        for media_id in media_ids:
            media_file = media_lookup[str(media_id)]
            if str(media_file.user_id) != str(user_id):
                raise ZionaError(
                    message="One or more media files do not belong to this user",
                    code=VALIDATION_ERROR,
                )
            if media_file.status == MediaStatus.FAILED:
                raise ZionaError(
                    message="One or more media files failed processing",
                    code=VALIDATION_ERROR,
                )
            if media_file.status != MediaStatus.READY:
                raise ZionaError(
                    message="One or more media files are still processing",
                    code=VALIDATION_ERROR,
                )
            resolved_media_files.append(media_file)

    if media_urls:
        for url in media_urls:
            normalized_url = (url or "").strip()
            if not normalized_url:
                continue
            if not re.match(URL_PATTERN, normalized_url):
                raise ZionaError(
                    message=f"Invalid media URL: {normalized_url}",
                    code=VALIDATION_ERROR,
                )

            inferred_type = _infer_media_type_from_url(normalized_url)
            resolved_media_files.append(
                MediaFile.objects.create(
                    user_id=user_id,
                    storage_path=normalized_url,
                    file_name=os.path.basename(urlparse(normalized_url).path) or "media_url",
                    file_type=_infer_file_type(normalized_url, inferred_type),
                    file_size=0,
                    media_type=inferred_type,
                    thumbnail_path=thumbnail_url if inferred_type == StoredMediaType.VIDEO else "",
                    width=width,
                    height=height,
                    duration=duration if inferred_type == StoredMediaType.VIDEO else None,
                    status=MediaStatus.READY,
                )
            )

    normalized_hint = _normalize_media_type_hint(media_type)
    resolved_types = {media_file.media_type for media_file in resolved_media_files}
    if len(resolved_types) > 1:
        raise ZionaError(
            message="Circle posts cannot mix image and video media in one post",
            code=VALIDATION_ERROR,
        )

    effective_type = next(iter(resolved_types), None)
    if normalized_hint and effective_type and normalized_hint != effective_type:
        raise ZionaError(
            message="Provided mediaType does not match the attached media",
            code=VALIDATION_ERROR,
        )

    return resolved_media_files, normalized_hint or effective_type


def get_circle_feed(
    circle_id: str,
    page: int = 1,
    page_size: int = 20,
    viewer_id: str | None = None,
    sort_by: str = "NEW",
    author_id: str | None = None,
) -> tuple[list[CirclePost], bool, int]:
    """
    Return paginated CirclePosts for a given circle.

    Args:
        sort_by: "TRENDING" orders by total engagement desc; "NEW" (default) by -created_at.
        author_id: When provided, filters posts to only those by this user ("My Posts").

    Annotates each post with is_liked_by_viewer and is_prayed_by_viewer
    using Exists subqueries — zero N+1 queries.

    Returns:
        (posts, has_next_page, total_count)
    """
    try:
        circle = Circle.objects.get(id=circle_id, is_active=True, deleted_at__isnull=True)
    except Circle.DoesNotExist:
        raise ZionaError(
            message="Circle does not exist or has been deleted", code=CIRCLE_NOT_FOUND
        ) from None

    queryset = (
        CirclePost.objects.filter(circle=circle, deleted_at__isnull=True)
        .select_related("user")
        .prefetch_related("media_files")
    )

    # ── Author filter ("My Posts" toggle) ──────────────────────────────────
    if author_id:
        queryset = queryset.filter(user_id=author_id)

    # ── Sorting ─────────────────────────────────────────────────────────────
    if sort_by == "TRENDING":
        # Annotate a single engagement_total so we sort in one DB pass.
        # Ties broken by recency so the feed stays fresh.
        queryset = queryset.annotate(
            engagement_total=F("likes_count") + F("comments_count") + F("prayed_count")
        ).order_by("-engagement_total", "-created_at")
    else:
        # "NEW" — most recent first (default)
        queryset = queryset.order_by("-created_at")

    # ── Viewer engagement state (zero N+1 via Exists subqueries) ───────────
    if viewer_id:
        queryset = queryset.annotate(
            is_liked_by_viewer=Exists(
                CirclePostEngagement.objects.filter(
                    post=OuterRef("pk"),
                    user_id=viewer_id,
                    engagement_type="like",
                )
            ),
            is_prayed_by_viewer=Exists(
                CirclePostEngagement.objects.filter(
                    post=OuterRef("pk"),
                    user_id=viewer_id,
                    engagement_type="pray",
                )
            ),
        )
    else:
        queryset = queryset.annotate(
            is_liked_by_viewer=Exists(CirclePostEngagement.objects.none()),
            is_prayed_by_viewer=Exists(CirclePostEngagement.objects.none()),
        )

    total_count = queryset.count()
    offset = (page - 1) * page_size
    posts = list(queryset[offset : offset + page_size + 1])

    has_next_page = len(posts) > page_size
    return posts[:page_size], has_next_page, total_count


def get_circle_post(post_id: str, viewer_id: str | None = None) -> CirclePost:
    """
    Fetch a single CirclePost by ID with viewer engagement annotations.
    Raises ZionaError(CIRCLE_POST_NOT_FOUND) if not found or soft-deleted.
    """
    queryset = (
        CirclePost.objects.filter(id=post_id, deleted_at__isnull=True)
        .select_related("user")
        .prefetch_related("media_files")
    )

    if viewer_id:
        queryset = queryset.annotate(
            is_liked_by_viewer=Exists(
                CirclePostEngagement.objects.filter(
                    post=OuterRef("pk"),
                    user_id=viewer_id,
                    engagement_type="like",
                )
            ),
            is_prayed_by_viewer=Exists(
                CirclePostEngagement.objects.filter(
                    post=OuterRef("pk"),
                    user_id=viewer_id,
                    engagement_type="pray",
                )
            ),
        )
    else:
        queryset = queryset.annotate(
            is_liked_by_viewer=Exists(CirclePostEngagement.objects.none()),
            is_prayed_by_viewer=Exists(CirclePostEngagement.objects.none()),
        )

    post = queryset.first()
    if not post:
        raise ZionaError(message="Post not found", code=CIRCLE_POST_NOT_FOUND)
    return post


@transaction.atomic
def like_circle_post(user_id: str, post_id: str) -> dict:
    """
    Toggle a like engagement on a CirclePost.
    Mirrors like_anchor — uses CirclePostEngagement with engagement_type='like'.
    Updates CirclePost.likes_count atomically via F() expressions.

    Returns:
        {"liked": bool, "likes_count": int}
    """
    try:
        post = CirclePost.objects.select_for_update().get(id=post_id, deleted_at__isnull=True)
    except CirclePost.DoesNotExist:
        raise ZionaError(message="Post not found", code=CIRCLE_POST_NOT_FOUND) from None

    engagement, created = CirclePostEngagement.objects.get_or_create(
        post=post,
        user_id=user_id,
        engagement_type="like",
    )

    if created:
        CirclePost.objects.filter(id=post_id).update(likes_count=F("likes_count") + 1)
        post.refresh_from_db(fields=["likes_count"])
        return {"liked": True, "likes_count": post.likes_count}

    engagement.delete()
    CirclePost.objects.filter(id=post_id).update(likes_count=F("likes_count") - 1)
    post.refresh_from_db(fields=["likes_count"])
    return {"liked": False, "likes_count": max(post.likes_count, 0)}


@transaction.atomic
def ensure_circle_post_liked(user_id: str, post_id: str) -> dict:
    """Idempotently ensure a CirclePost has a like from the viewer."""
    try:
        post = CirclePost.objects.select_for_update().get(id=post_id, deleted_at__isnull=True)
    except CirclePost.DoesNotExist:
        raise ZionaError(message="Post not found", code=CIRCLE_POST_NOT_FOUND) from None

    _engagement, created = CirclePostEngagement.objects.get_or_create(
        post=post,
        user_id=user_id,
        engagement_type="like",
    )

    if created:
        CirclePost.objects.filter(id=post_id).update(likes_count=F("likes_count") + 1)
        post.refresh_from_db(fields=["likes_count"])

    return {"liked": True, "likes_count": max(post.likes_count, 0)}


@transaction.atomic
def create_circle_post(
    user_id: str,
    circle_id: str,
    text: str = "",
    media_ids: list[str] | None = None,
    media_urls: list[str] | None = None,
    media_type: str | None = None,
    thumbnail_url: str | None = None,
    width: int | None = None,
    height: int | None = None,
    duration: int | None = None,
) -> CirclePost:
    """
    Create a post inside a Circle.

    Raises:
        ZionaError(CIRCLE_NOT_FOUND) if the circle does not exist.
        ZionaError(NOT_MEMBER) if the user is not a circle member.
        ZionaError(VALIDATION_ERROR) if no content is provided or media is invalid.
    """
    try:
        circle = Circle.objects.get(id=circle_id, is_active=True, deleted_at__isnull=True)
    except Circle.DoesNotExist:
        raise ZionaError(
            message="Circle does not exist or has been deleted", code=CIRCLE_NOT_FOUND
        ) from None

    if not CircleMembership.objects.filter(circle=circle, user_id=user_id).exists():
        raise ZionaError(
            message="You must be a member of this Circle to post",
            code=NOT_MEMBER,
        )

    trimmed_text = (text or "").strip()
    normalized_media_ids = [
        str(media_id).strip() for media_id in (media_ids or []) if str(media_id).strip()
    ]
    normalized_media_urls = [
        (url or "").strip() for url in (media_urls or []) if (url or "").strip()
    ]

    if not any([trimmed_text, normalized_media_ids, normalized_media_urls]):
        raise ZionaError(
            message="A post must include text or at least one media attachment",
            code=VALIDATION_ERROR,
        )

    resolved_media_files, _ = _resolve_circle_post_media(
        user_id=user_id,
        media_ids=normalized_media_ids,
        media_urls=normalized_media_urls,
        media_type=media_type,
        thumbnail_url=thumbnail_url,
        width=width,
        height=height,
        duration=duration,
    )

    post = CirclePost.objects.create(
        circle=circle,
        user_id=user_id,
        text=trimmed_text,
    )
    if resolved_media_files:
        post.media_files.set(resolved_media_files)

    return CirclePost.objects.select_related("user").prefetch_related("media_files").get(id=post.id)


@transaction.atomic
def pray_for_anchor(user_id: str, anchor_id: str) -> dict:
    """
    Toggle a pray engagement on an Anchor.
    Creates the engagement if it does not exist, deletes it if it does.
    Updates Anchor.prayed_count atomically using F() expressions.

    Returns:
        {"prayed": bool, "prayed_count": int}
    """
    try:
        anchor = Anchor.objects.select_for_update().get(id=anchor_id, deleted_at__isnull=True)
    except Anchor.DoesNotExist:
        raise ZionaError(message="Anchor not found", code=ANCHOR_NOT_FOUND) from None

    engagement, created = AnchorEngagement.objects.get_or_create(
        anchor=anchor, user_id=user_id, engagement_type="pray"
    )

    if created:
        Anchor.objects.filter(id=anchor_id).update(prayed_count=F("prayed_count") + 1)
        anchor.refresh_from_db(fields=["prayed_count"])
        return {"prayed": True, "prayed_count": anchor.prayed_count}
    engagement.delete()
    Anchor.objects.filter(id=anchor_id).update(prayed_count=F("prayed_count") - 1)
    anchor.refresh_from_db(fields=["prayed_count"])
    return {"prayed": False, "prayed_count": max(anchor.prayed_count, 0)}


@transaction.atomic
def like_anchor(user_id: str, anchor_id: str) -> dict:
    """
    Toggle a like engagement on an Anchor.
    Updates Anchor.anchor_liked_count atomically using F() expressions.

    Returns:
        {"liked": bool, "anchor_liked_count": int}
    """
    try:
        anchor = Anchor.objects.select_for_update().get(id=anchor_id, deleted_at__isnull=True)
    except Anchor.DoesNotExist:
        raise ZionaError(message="Anchor not found", code=ANCHOR_NOT_FOUND) from None

    engagement, created = AnchorEngagement.objects.get_or_create(
        anchor=anchor, user_id=user_id, engagement_type="like"
    )

    if created:
        Anchor.objects.filter(id=anchor_id).update(anchor_liked_count=F("anchor_liked_count") + 1)
        anchor.refresh_from_db(fields=["anchor_liked_count"])
        return {"liked": True, "anchor_liked_count": anchor.anchor_liked_count}
    engagement.delete()
    Anchor.objects.filter(id=anchor_id).update(anchor_liked_count=F("anchor_liked_count") - 1)
    anchor.refresh_from_db(fields=["anchor_liked_count"])
    return {"liked": False, "anchor_liked_count": max(anchor.anchor_liked_count, 0)}


@transaction.atomic
def pray_for_circle_post(user_id: str, post_id: str) -> dict:
    """
    Toggle a pray engagement on a CirclePost.
    Updates CirclePost.prayed_count atomically using F() expressions.

    Returns:
        {"prayed": bool, "prayed_count": int}
    """
    try:
        post = CirclePost.objects.select_for_update().get(id=post_id, deleted_at__isnull=True)
    except CirclePost.DoesNotExist:
        raise ZionaError(message="Post not found", code=CIRCLE_POST_NOT_FOUND) from None

    engagement, created = CirclePostEngagement.objects.get_or_create(
        post=post,
        user_id=user_id,
        engagement_type="pray",
    )

    if created:
        CirclePost.objects.filter(id=post_id).update(prayed_count=F("prayed_count") + 1)
        post.refresh_from_db(fields=["prayed_count"])
        return {"prayed": True, "prayed_count": post.prayed_count}
    engagement.delete()
    CirclePost.objects.filter(id=post_id).update(prayed_count=F("prayed_count") - 1)
    post.refresh_from_db(fields=["prayed_count"])
    return {"prayed": False, "prayed_count": max(post.prayed_count, 0)}

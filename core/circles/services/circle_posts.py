"""Circle posts — feed, retrieval, creation, engagement.

Split from the former core/circles/services.py (no behavior change).
"""

import logging
import mimetypes
import os
from urllib.parse import urlparse

from django.db import transaction
from django.db.models import Exists, F, OuterRef

from core.circles.access import require_circle_membership
from core.circles.models import (
    Circle,
    CircleMembership,
    CirclePost,
    CirclePostEngagement,
)
from core.engagement.hidden_content import exclude_hidden_circle_content
from core.media.models import MediaFile, MediaStatus
from core.media.models import MediaType as StoredMediaType
from core.media.services import validate_trusted_external_image_url
from core.shared.exceptions import ZionaError

logger = logging.getLogger("core.circles")

CIRCLE_POST_NOT_FOUND = "CIRCLE_POST_NOT_FOUND"
ANCHOR_NOT_FOUND = "ANCHOR_NOT_FOUND"
VALIDATION_ERROR = "VALIDATION_ERROR"

from core.circles.services.membership import get_circle_by_id  # noqa: E402

# Shorthand error codes
CIRCLE_NOT_FOUND = "CIRCLE_NOT_FOUND"
CIRCLE_INACTIVE = "CIRCLE_INACTIVE"
ALREADY_MEMBER = "ALREADY_MEMBER"
NOT_MEMBER = "NOT_MEMBER"


def _normalize_media_type_hint(media_type: str | None) -> str | None:
    if not media_type:
        return None
    value = getattr(media_type, "value", media_type)
    return str(value).strip().lower() or None


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
            normalized_url = validate_trusted_external_image_url(normalized_url)
            inferred_type = StoredMediaType.IMAGE
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
    circle = get_circle_by_id(circle_id, viewer_id=viewer_id)
    if not circle:
        raise ZionaError(message="Circle does not exist or has been deleted", code=CIRCLE_NOT_FOUND)

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
        .select_related("user", "circle")
        .prefetch_related("media_files")
    )
    queryset = exclude_hidden_circle_content(
        queryset,
        viewer_id,
        target_type="circle",
        target_field="circle_id",
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
    if not post or not post.circle.is_active or post.circle.deleted_at:
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
    require_circle_membership(
        user_id,
        str(post.circle_id),
        code=NOT_MEMBER,
        message="You must be a member of this Circle to like posts",
    )

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
    require_circle_membership(
        user_id,
        str(post.circle_id),
        code=NOT_MEMBER,
        message="You must be a member of this Circle to like posts",
    )

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

    full_post = (
        CirclePost.objects.select_related("user").prefetch_related("media_files").get(id=post.id)
    )

    # Dispatch @mention notifications — scoped to circle members only.
    try:
        from core.notifications.services import notify_mentions

        notify_mentions(
            text=trimmed_text,
            actor=full_post.user,
            reference_id=post.id,
            reference_type="circle_post",
            circle_id=str(circle_id),
        )
    except Exception:  # noqa: BLE001
        logger.warning("Failed to dispatch mention notifications for circle post %s", post.id)

    return full_post


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
    require_circle_membership(
        user_id,
        str(post.circle_id),
        code=NOT_MEMBER,
        message="You must be a member of this Circle to pray for posts",
    )

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

"""
Post service — business logic for creating, reading, updating, and deleting posts.

Validates post type rules, manages media attachments, and handles cache invalidation.
"""

import logging
from datetime import datetime, timedelta, timezone

from django.conf import settings

from core.engagement.models import Like, Save
from core.follows.models import Follow
from core.posts.models import Post, PostType
from core.posts.selectors import PostSelector
from core.scripture.constants import normalize_translation
from core.shared.dtos import (
    AuthorDTO,
    ImageMediaDTO,
    MediaItemDTO,
    PostResponseDTO,
    ScriptureDTO,
    StatsDTO,
    TextMediaDTO,
    VideoMediaDTO,
    ViewerStateDTO,
)
from core.shared.exceptions import ErrorCode, PostError
from core.shared.utils import build_post_share_url

logger = logging.getLogger("core.posts")

TEXT_POST_MAX_CAPTION = 500
MEDIA_POST_MAX_CAPTION = 2200
IMAGE_MIN_COUNT = 1
IMAGE_MAX_COUNT = 5
VIDEO_MAX_DURATION = 90
MAX_VIDEOS_PER_POST = 1
POST_EDIT_WINDOW_HOURS = 24
POST_ALLOWED_MEDIA_TYPES = ["image", "video"]


def _build_post_media_validation_details(**kwargs) -> dict:
    """Build consistent post media validation details."""
    from core.media.services import build_media_validation_details

    return build_media_validation_details(allowed_types=POST_ALLOWED_MEDIA_TYPES, **kwargs)


class PostService:
    """Service handling post lifecycle operations.

    Methods:
        create_post: Create a new post with media attachments
        get_post: Retrieve a post with viewer context
        update_post: Update post caption within edit window
        delete_post: Soft-delete a post
    """

    @staticmethod
    def create_post(
        user_id: str,
        post_type: str,
        caption: str | None = None,
        category_id: str | None = None,
        media_ids: list[str] | None = None,
        media_urls: list[str] | None = None,
        media_type: str | None = None,
        thumbnail_url: str | None = None,
        width: int | None = None,
        height: int | None = None,
        duration: int | None = None,
        scripture_reference: dict | None = None,
    ) -> PostResponseDTO:
        """Create a new post matching the agreed mobile contract.

        Args:
            user_id: UUID of the post author.
            post_type: Post type string (text, image, video).
            caption: Post caption text.
            category_id: Optional faith category ID.
            media_ids: List of media file UUIDs.
            media_type: Optional media type hint (IMAGE/VIDEO).
            scripture_reference: Optional dict with book, chapter, verse_start, etc.

        Returns:
            PostResponseDTO with full post data.

        Raises:
            PostError: If validation fails.
        """
        # 0. Validate Caption Length
        if caption:
            max_len = (
                TEXT_POST_MAX_CAPTION if post_type.lower() == "text" else MEDIA_POST_MAX_CAPTION
            )
            if scripture_reference and post_type.lower() == "text" and len(caption) > 500:
                raise PostError(
                    message="Caption limited to 500 characters for posts with scripture",
                    code="CAPTION_TOO_LONG",
                    extensions={"field": "caption", "maxLength": 500},
                )

            if len(caption) > max_len:
                raise PostError(
                    message=f"Caption limited to {max_len} characters",
                    code="CAPTION_TOO_LONG",
                    extensions={"field": "caption", "maxLength": max_len},
                )

        # 0.5. Media Required Validation (Prevention)
        # Ensure that if the post is MEDIA, it actually has media provided.
        if post_type.upper() in ("MEDIA", "IMAGE", "VIDEO") and not media_ids and not media_urls:
            raise PostError(
                message="Media files (IDs or URLs) are required for media posts.",
                code="MEDIA_REQUIRED",
            )

        from django.db.models import Q

        from core.categories.models import Category
        from core.media.models import MediaFile, MediaStatus
        from core.media.services import validate_trusted_external_image_url
        from core.users.models import User

        category_obj = None
        if category_id:
            category_obj = Category.objects.filter(Q(id=category_id) | Q(slug=category_id)).first()
            if not category_obj:
                raise PostError(
                    message=f"Category '{category_id}' not found", code="INVALID_CATEGORY"
                )

        # 1. Resolve Media IDs or URLs
        resolved_media_files = []
        if media_ids:
            resolved_media_files = list(MediaFile.objects.filter(id__in=media_ids))
            if len(resolved_media_files) != len(media_ids):
                raise PostError(
                    message="One or more media IDs not found",
                    code=ErrorCode.VALIDATION_ERROR,
                )
            for media_file in resolved_media_files:
                if str(media_file.user_id) != str(user_id):
                    raise PostError(
                        message="One or more media IDs do not belong to this user",
                        code=ErrorCode.VALIDATION_ERROR,
                    )
                if media_file.status == MediaStatus.FAILED:
                    raise PostError(
                        message="One or more media files failed processing",
                        code=ErrorCode.VALIDATION_ERROR,
                    )
                if media_file.status != MediaStatus.READY:
                    raise PostError(
                        message="One or more media files are still processing",
                        code=ErrorCode.VALIDATION_ERROR,
                    )
        elif media_urls:
            for url in media_urls:
                normalized_url = validate_trusted_external_image_url(url)
                media_file = MediaFile.objects.create(
                    user_id=user_id,
                    storage_path=normalized_url,
                    file_name=normalized_url.split("/")[-1] or "media_url",
                    file_type="image/jpeg",
                    file_size=0,
                    media_type="image",
                    thumbnail_path="",
                    width=width,
                    height=height,
                    duration=None,
                    status="ready",
                )
                resolved_media_files.append(media_file)

        video_media_files = [m for m in resolved_media_files if m.media_type == "video"]
        if len(video_media_files) > MAX_VIDEOS_PER_POST:
            raise PostError(
                message="Only one video is allowed per post.",
                code="MULTIPLE_VIDEOS_NOT_ALLOWED",
                extensions=_build_post_media_validation_details(
                    received_videos_count=len(video_media_files),
                ),
            )

        for media_file in video_media_files:
            if media_file.duration is None:
                continue
            if media_file.duration > VIDEO_MAX_DURATION:
                raise PostError(
                    message=f"Video duration exceeds the {VIDEO_MAX_DURATION}-second limit.",
                    code=ErrorCode.VIDEO_TOO_LONG,
                    extensions=_build_post_media_validation_details(
                        received_duration_seconds=media_file.duration,
                    ),
                )

        # 2. Handle Scripture
        scripture_fields = {}
        if scripture_reference:
            from core.scripture.services import ScriptureError, ScriptureService

            try:
                verse_data = ScriptureService.fetch_verse(
                    book=scripture_reference.get("book"),
                    chapter=scripture_reference.get("chapter"),
                    verse_start=scripture_reference.get("verse_start"),
                    verse_end=scripture_reference.get("verse_end"),
                    version=scripture_reference.get("version", "kjv"),
                )
                scripture_fields = {
                    "scripture_book": verse_data["book"],
                    "scripture_chapter": verse_data["chapter"],
                    "scripture_verse_start": verse_data["verse_start"],
                    "scripture_verse_end": verse_data["verse_end"],
                    # normalize_translation converts verbose CDN display names
                    # (e.g. "King James Version") to the short code stored in
                    # the DB column (VARCHAR 10), e.g. "KJV".
                    "scripture_translation": normalize_translation(verse_data["version"]),
                }
            except ScriptureError as e:
                raise PostError(message=e.message, code=ErrorCode.VALIDATION_ERROR) from e

        # 3. Create Post Record
        try:
            user = User.objects.get(id=user_id, deleted_at__isnull=True)
        except User.DoesNotExist:
            raise PostError(message="User not found", code=ErrorCode.USER_NOT_FOUND) from None

        # Map mobile types to internal models
        # Note for mobile engineers: GraphQL Enum is TEXT, MEDIA, BIBLE
        # Django internal representations are text, image, video.
        internal_post_type = post_type.lower()
        if post_type == "TEXT":
            internal_post_type = "text"
        if post_type == "MEDIA":
            internal_post_type = "image"  # Default to image
        if post_type == "BIBLE":
            internal_post_type = "text"  # Bible posts are text-based natively

        # Refine type based on resolved media
        if media_type == "VIDEO" or any(m.media_type == "video" for m in resolved_media_files):
            internal_post_type = "video"
        elif any(m.media_type == "image" for m in resolved_media_files):
            internal_post_type = "image"

        post = Post.objects.create(
            user=user,
            post_type=internal_post_type,
            caption=caption or "",
            category=category_obj,
            media_count=len(resolved_media_files),
            **scripture_fields,
        )

        # 4. Associate Media
        if resolved_media_files:
            post.media_files.set(resolved_media_files)

        # 5. Invalidate user stats cache so `me.stats.postsCount` reflects immediately.
        try:
            from django.core.cache import cache

            cache.delete(f"user_me_data_{user_id}")
        except (ConnectionError, TimeoutError, OSError):
            logger.warning("Failed to clear user_me_data cache after create_post")

        # 6. Invalidate followers' feed caches asynchronously.
        try:
            from core.feed.tasks import invalidate_followers_feed_cache

            invalidate_followers_feed_cache.delay(str(user.id))
        except Exception:  # noqa: BLE001 — Celery broker can raise arbitrary connection errors
            logger.warning("Failed to queue feed cache invalidation")

        # 7. Fan-out post to followers' Redis feed inboxes (async).
        try:
            from core.feed.tasks import fan_out_post_to_inboxes

            fan_out_post_to_inboxes.delay(str(post.id), str(user.id))
        except Exception:  # noqa: BLE001 — Celery broker can raise arbitrary connection errors
            logger.warning("Failed to queue feed inbox fan-out")

        # 8. Increment cached active-post counter.
        try:
            from core.shared.counter_cache import post_counter

            post_counter.increment()
        except (ConnectionError, TimeoutError, OSError):
            logger.warning("Failed to increment post counter cache")

        return PostService._build_post_dto(
            post,
            resolved_media_files,
            viewer_id=str(user.id),
            is_owner=True,
        )

    @staticmethod
    def get_post(post_id: str, viewer_id: str | None = None) -> PostResponseDTO:
        """Retrieve a single post with viewer context.

        Args:
            post_id: UUID of the post.
            viewer_id: UUID of the viewing user (optional).

        Returns:
            PostResponseDTO with viewer-specific state.

        Raises:
            PostError: If post not found or deleted.
        """
        post = PostSelector.get_post_with_context(post_id)
        if not post:
            raise PostError(
                message="Post not found",
                code=ErrorCode.POST_NOT_FOUND,
            )

        media_items = list(post.media_files.all())
        return PostService._build_post_dto(post, media_items, viewer_id=viewer_id)

    @staticmethod
    def update_post(
        post_id: str,
        user_id: str,
        caption: str | None = None,
    ) -> PostResponseDTO:
        """Update a post's caption within the edit window.

        Args:
            post_id: UUID of the post.
            user_id: UUID of the requesting user.
            caption: New caption text.

        Returns:
            Updated PostResponseDTO.

        Raises:
            PostError: If ownership check or edit window fails.
        """
        from core.users.models import User

        post = PostSelector.get_post_with_context(post_id)
        if not post:
            raise PostError(message="Post not found", code=ErrorCode.POST_NOT_FOUND)

        user = User.objects.filter(id=user_id).first()
        if not user:
            raise PostError(message="User not found", code=ErrorCode.USER_NOT_FOUND)

        if str(post.user_id) != user_id and not user.is_admin:
            raise PostError(
                message="You do not have permission to edit this post",
                code=ErrorCode.PERMISSION_DENIED,
            )

        edit_deadline = post.created_at + timedelta(hours=POST_EDIT_WINDOW_HOURS)
        if datetime.now(timezone.utc) > edit_deadline:
            raise PostError(
                message="Edit window has expired (24 hours)",
                code=ErrorCode.POST_EDIT_WINDOW_EXPIRED,
            )

        if caption is not None:
            max_len = (
                TEXT_POST_MAX_CAPTION if post.post_type == PostType.TEXT else MEDIA_POST_MAX_CAPTION
            )
            if len(caption) > max_len:
                raise PostError(
                    message=f"Caption limited to {max_len} characters",
                    code=ErrorCode.TEXT_POST_TOO_LONG,
                )
            post.caption = caption

        post.save(update_fields=["caption", "updated_at"])

        # Invalidate me-data cache so recent_posts caption is always fresh.
        # Matches the same pattern already used in create_post() and delete_post().
        try:
            from django.core.cache import cache

            cache.delete(f"user_me_data_{user_id}")
        except (ConnectionError, TimeoutError, OSError):
            logger.warning("Failed to clear user_me_data cache after update_post")

        logger.info(
            "post_updated",
            extra={"post_id": str(post.id), "user_id": user_id},
        )

        media_items = list(post.media_files.all())
        return PostService._build_post_dto(post, media_items, viewer_id=user_id)

    @staticmethod
    def delete_post(post_id: str, user_id: str) -> bool:
        """Soft-delete a post.

        Args:
            post_id: UUID of the post.
            user_id: UUID of the requesting user.

        Returns:
            True if successfully deleted.

        Raises:
            PostError: If post not found or permission denied.
        """
        from core.users.models import User

        try:
            post = Post.objects.get(id=post_id, deleted_at__isnull=True)
        except Post.DoesNotExist:
            raise PostError(message="Post not found", code=ErrorCode.POST_NOT_FOUND) from None

        user = User.objects.filter(id=user_id).first()
        if not user:
            raise PostError(message="User not found", code=ErrorCode.USER_NOT_FOUND)

        if str(post.user_id) != user_id and not user.is_admin:
            raise PostError(
                message="You do not have permission to delete this post",
                code=ErrorCode.PERMISSION_DENIED,
            )

        post.soft_delete()

        # Invalidate user stats cache so `me.stats.postsCount` reflects immediately.
        try:
            from django.core.cache import cache

            cache.delete(f"user_me_data_{user_id}")
        except (ConnectionError, TimeoutError, OSError):
            logger.warning("Failed to clear user_me_data cache after delete_post")

        # Remove post from followers' Redis feed inboxes.
        try:
            from core.feed.tasks import remove_post_from_inboxes

            remove_post_from_inboxes.delay(str(post.id), str(post.user_id))
        except Exception:  # noqa: BLE001 — Celery broker can raise arbitrary connection errors
            logger.warning("Failed to queue feed inbox removal")

        # Decrement cached active-post counter.
        try:
            from core.shared.counter_cache import post_counter

            post_counter.decrement()
        except (ConnectionError, TimeoutError, OSError):
            logger.warning("Failed to decrement post counter cache")

        logger.info(
            "post_deleted",
            extra={"post_id": str(post.id), "user_id": user_id},
        )

        return True

    @staticmethod
    def _build_post_dto(
        post: Post,
        media_items: list | None = None,
        viewer_id: str | None = None,
        is_owner: bool | None = None,
        liked_post_ids: set | None = None,
        saved_post_ids: set | None = None,
        following_user_ids: set | None = None,
        followed_by_user_ids: set | None = None,
    ) -> PostResponseDTO:
        """Build a PostResponseDTO from a Post model instance.

        Args:
            post: The Post instance.
            media_items: List of PostMedia instances.
            viewer_id: Optional viewer user ID for viewer state.
            is_owner: Override for ownership flag.

        Returns:
            Fully populated PostResponseDTO.
        """
        author = AuthorDTO(
            id=str(post.user_id),
            username=post.user.username or "",
            avatar_url=post.user.avatar_url or None,
        )

        if (post.post_type in [PostType.IMAGE, "image", "image_post"]) and media_items:
            media_items_dto = []
            # Sort if they have order attr (legacy), otherwise by created_at for deterministic upload order
            sorted_items = sorted(
                media_items or [],
                key=lambda x: (
                    getattr(x, "order", 999),
                    getattr(x, "created_at", getattr(x, "id", "")),
                ),
            )
            for m in sorted_items:
                m_id = str(getattr(m, "id", ""))
                m_url = getattr(m, "url", getattr(m, "media_url", ""))
                m_width = getattr(m, "width", 0)
                m_height = getattr(m, "height", 0)
                media_items_dto.append(
                    MediaItemDTO(
                        id=m_id,
                        url=m_url,
                        width=m_width,
                        height=m_height,
                        order=getattr(m, "order", 0),
                    )
                )
            media = ImageMediaDTO(items=media_items_dto)
            actual_type = "image"
        elif (post.post_type in [PostType.VIDEO, "video"]) and media_items:
            v = media_items[0]
            v_url = getattr(v, "url", getattr(v, "media_url", ""))
            v_thumb = getattr(v, "thumbnail_url", getattr(v, "thumbnail_path", ""))
            v_duration = getattr(v, "duration", 0)
            media = VideoMediaDTO(
                url=v_url,
                thumbnail_url=v_thumb or "",
                duration=v_duration or 0,
                width=getattr(v, "width", 0),
                height=getattr(v, "height", 0),
            )
            actual_type = "video"
        else:
            media = TextMediaDTO()
            actual_type = "text"

            # Senior-level Logging for Data Corruption
            # If the database thinks it's a media post, but we have no media records.
            if post.post_type in [PostType.VIDEO, "video", PostType.IMAGE, "image"]:
                logger.error(
                    "DATA_CORRUPTION: Post marked as media but has no attached files",
                    extra={
                        "post_id": str(post.id),
                        "post_type": post.post_type,
                        "author_id": str(post.user_id),
                    },
                )

            # We now allow TEXT posts with scripture natively.
            # Using the exact post.post_type stored in DB (text, video, image)
            actual_type = post.post_type

        def _get_count(post, attr, fallback_qs=None):
            val = getattr(post, attr, None)
            if val is not None:
                return val
            return fallback_qs.count() if fallback_qs is not None else 0

        hide_likes = getattr(post.user, "hide_like_count", False)

        stats = StatsDTO(
            likes_count=0 if hide_likes else _get_count(post, "likes_count", post.likes),
            comments_count=_get_count(
                post, "comments_count", post.comments.filter(deleted_at__isnull=True)
            ),
            shares_count=_get_count(post, "shares_count", post.shares),
            saves_count=_get_count(post, "saves_count", post.saves),
        )

        viewer_state = None
        if viewer_id:
            post_id_str = str(post.id)
            user_id_str = str(post.user_id)

            # Use bulk-fetched sets if available, otherwise fall back to individual queries
            if liked_post_ids is not None:
                is_liked = post_id_str in liked_post_ids
            else:
                is_liked = Like.objects.filter(user_id=viewer_id, post_id=post.id).exists()

            if saved_post_ids is not None:
                is_saved = post_id_str in saved_post_ids
            else:
                is_saved = Save.objects.filter(user_id=viewer_id, post_id=post.id).exists()

            if following_user_ids is not None:
                is_following = (
                    user_id_str in following_user_ids if user_id_str != viewer_id else False
                )
            else:
                is_following = (
                    Follow.objects.filter(follower_id=viewer_id, following_id=post.user_id).exists()
                    if user_id_str != viewer_id
                    else False
                )

            if followed_by_user_ids is not None:
                is_followed_by = (
                    user_id_str in followed_by_user_ids if user_id_str != viewer_id else False
                )
            else:
                is_followed_by = (
                    Follow.objects.filter(follower_id=post.user_id, following_id=viewer_id).exists()
                    if user_id_str != viewer_id
                    else False
                )

            viewer_state = ViewerStateDTO(
                liked=is_liked,
                saved=is_saved,
                following_author=is_following,
                followed_by_author=is_followed_by,
                is_owner=is_owner if is_owner is not None else (user_id_str == viewer_id),
            )

            # Contract invariant for mobile: if the viewer has liked the post,
            # the visible count must include that like. This protects clients
            # from stale annotations or privacy/fallback paths that would
            # otherwise produce viewerState.liked=true with likesCount=0.
            if is_liked and stats.likes_count < 1:
                stats.likes_count = 1

        scripture = None
        if post.scripture_book and post.scripture_chapter and post.scripture_verse_start:
            from core.scripture.services import ScriptureService

            try:
                result = ScriptureService.fetch_verse(
                    book=post.scripture_book,
                    chapter=post.scripture_chapter,
                    verse_start=post.scripture_verse_start,
                    verse_end=post.scripture_verse_end,
                    version=post.scripture_translation or "KJV",
                )
                scripture = ScriptureDTO(
                    reference=result["reference"],
                    text=result["text"],
                    version=result["version"],
                    book=result["book"],
                    chapter=result["chapter"],
                    verse_start=result["verse_start"],
                    verse_end=result["verse_end"],
                    verses=result.get("verses", []),
                )
            except Exception:
                reference = (
                    f"{post.scripture_book} {post.scripture_chapter}:{post.scripture_verse_start}"
                )
                if post.scripture_verse_end:
                    reference += f"-{post.scripture_verse_end}"
                scripture = ScriptureDTO(
                    reference=reference,
                    text="",
                    version=post.scripture_translation or "KJV",
                    book=post.scripture_book,
                    chapter=post.scripture_chapter,
                    verse_start=post.scripture_verse_start,
                    verse_end=post.scripture_verse_end,
                    verses=[],
                )

        return PostResponseDTO(
            id=str(post.id),
            type=actual_type,
            created_at=post.created_at.isoformat(),
            caption=post.caption or None,
            category_id=str(post.category_id) if post.category_id else None,
            author=author,
            media=media,
            stats=stats,
            viewer_state=viewer_state,
            share_url=build_post_share_url(settings.APP_SHARE_BASE_URL, str(post.id)),
            scripture=scripture,
        )

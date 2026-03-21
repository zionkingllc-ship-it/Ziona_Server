"""
Post service — business logic for creating, reading, updating, and deleting posts.

Validates post type rules, manages media attachments, and handles cache invalidation.
"""

import logging
from datetime import datetime, timedelta, timezone

from core.engagement.models import Like, Save
from core.follows.models import Follow
from core.posts.models import Post, PostType
from core.posts.selectors import PostSelector
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

logger = logging.getLogger("core.posts")

TEXT_POST_MAX_CAPTION = 500
MEDIA_POST_MAX_CAPTION = 2200
IMAGE_MIN_COUNT = 1
IMAGE_MAX_COUNT = 5
VIDEO_MIN_DURATION = 60
VIDEO_MAX_DURATION = 80
POST_EDIT_WINDOW_HOURS = 24


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

        import re

        from core.categories.models import Category
        from core.media.models import MediaFile
        from core.users.models import User

        if category_id and not Category.objects.filter(id=category_id).exists():
            raise PostError(message="Invalid category ID", code="INVALID_CATEGORY")

        # 1. Resolve Media IDs or URLs
        resolved_media_files = []
        if media_ids:
            resolved_media_files = list(MediaFile.objects.filter(id__in=media_ids))
            if len(resolved_media_files) != len(media_ids):
                raise PostError(
                    message="One or more media IDs not found",
                    code=ErrorCode.VALIDATION_ERROR,
                )
        elif media_urls:
            url_pattern = re.compile(
                r"^(?:http|ftp)s?://"
                r"(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)|"
                r"localhost|"
                r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})"
                r"(?::\d+)?"
                r"(?:/?|[/?]\S+)$",
                re.IGNORECASE,
            )

            for url in media_urls:
                if not re.match(url_pattern, url):
                    raise PostError(message=f"Invalid media URL: {url}", code="INVALID_MEDIA_URL")

                ext = url.split("?")[0].split(".")[-1].lower() if "." in url.split("?")[0] else ""
                inferred_type = "video" if ext in ["mp4", "mov", "webm"] else "image"
                media_file = MediaFile.objects.create(
                    user_id=user_id,
                    storage_path=url,
                    file_name=url.split("/")[-1] or "media_url",
                    file_type="video/mp4" if inferred_type == "video" else "image/jpeg",
                    file_size=0,
                    media_type=inferred_type,
                    thumbnail_path=thumbnail_url if thumbnail_url else "",
                    width=width,
                    height=height,
                    duration=duration if inferred_type == "video" else None,
                    status="ready",
                )
                resolved_media_files.append(media_file)

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
                    "scripture_version": verse_data["version"],
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
            category_id=category_id,
            media_count=len(resolved_media_files),
            **scripture_fields,
        )

        # 4. Associate Media
        if resolved_media_files:
            post.media_files.set(resolved_media_files)

        # 5. Invalidate Cache
        try:
            from core.feed.tasks import invalidate_followers_feed_cache

            invalidate_followers_feed_cache.delay(str(user.id))
        except Exception:
            logger.warning("Failed to queue feed cache invalidation")

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

        if post.post_type in [PostType.IMAGE, "image", "image_post"]:
            media_items_dto = []
            # Sort if they have order attr, otherwise use as is
            sorted_items = sorted(media_items or [], key=lambda x: getattr(x, "order", 0))
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
        else:
            media = TextMediaDTO()

        def _get_count(post, attr, fallback_qs=None):
            val = getattr(post, attr, None)
            if val is not None:
                return val
            return fallback_qs.count() if fallback_qs is not None else 0

        stats = StatsDTO(
            likes_count=_get_count(post, "likes_count", post.likes),
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

            viewer_state = ViewerStateDTO(
                liked=is_liked,
                saved=is_saved,
                following_author=is_following,
                is_owner=is_owner if is_owner is not None else (user_id_str == viewer_id),
            )

        scripture = None
        if post.scripture_book and post.scripture_chapter and post.scripture_verse_start:
            from core.scripture.services import ScriptureService

            try:
                result = ScriptureService.fetch_verse(
                    book=post.scripture_book,
                    chapter=post.scripture_chapter,
                    verse_start=post.scripture_verse_start,
                    verse_end=post.scripture_verse_end,
                    version=post.scripture_version or "KJV",
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
                    version=post.scripture_version or "KJV",
                    book=post.scripture_book,
                    chapter=post.scripture_chapter,
                    verse_start=post.scripture_verse_start,
                    verse_end=post.scripture_verse_end,
                    verses=[],
                )

        return PostResponseDTO(
            id=str(post.id),
            type=str(post.post_type),
            created_at=post.created_at.isoformat(),
            caption=post.caption or None,
            category_id=str(post.category) if post.category else None,
            author=author,
            media=media,
            stats=stats,
            viewer_state=viewer_state,
            share_url=f"https://ziona.app/post/{post.id}",
            scripture=scripture,
        )

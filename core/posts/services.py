"""
Post service — business logic for creating, reading, updating, and deleting posts.

Validates post type rules, manages media attachments, and handles cache invalidation.
"""

import logging
from datetime import datetime, timedelta, timezone

from core.engagement.models import Like, Save
from core.follows.models import Follow
from core.posts.models import Post, PostCategory, PostMedia, PostType
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
        media_items: list[dict] | None = None,
        category: str | None = None,
        scripture_book: str | None = None,
        scripture_chapter: int | None = None,
        scripture_verse_start: int | None = None,
        scripture_verse_end: int | None = None,
        scripture_version: str = "KJV",
    ) -> PostResponseDTO:
        """Create a new post and distribute to followers.idation and media linking.

        Args:
            user_id: UUID of the post author.
            post_type: Content type (image, video, text).
            caption: Post text content.
            media_items: List of media dicts with url, type, thumbnail_url,
                         width, height, duration, order.
            category: Optional faith category.

        Returns:
            PostResponseDTO with full post data.

        Raises:
            PostError: If validation fails.
        """
        from core.users.models import User

        media_items = media_items or []

        # Validate post type
        if post_type not in PostType.values:
            raise PostError(
                message=f"Invalid post type: {post_type}",
                code=ErrorCode.INVALID_POST_TYPE,
            )

        # Validate category
        if category and category not in PostCategory.values:
            raise PostError(
                message=f"Invalid category: {category}",
                code=ErrorCode.INVALID_CATEGORY,
            )

        scripture_text = ""
        scripture_fields = {}

        # Type-specific validation
        if post_type == PostType.TEXT:
            if not caption and not scripture_book:
                raise PostError(
                    message="Text posts must have a caption or scripture.",
                    code=ErrorCode.VALIDATION_ERROR,
                )

            if scripture_book and scripture_chapter and scripture_verse_start:
                from core.scripture.services import ScriptureError, ScriptureService

                try:
                    verse_data = ScriptureService.fetch_verse(
                        book=scripture_book,
                        chapter=scripture_chapter,
                        verse_start=scripture_verse_start,
                        verse_end=scripture_verse_end,
                        version=scripture_version,
                    )
                    scripture_text = verse_data["text"]
                    scripture_fields = {
                        "scripture_book": verse_data["book"],
                        "scripture_chapter": verse_data["chapter"],
                        "scripture_verse_start": verse_data["verse_start"],
                        "scripture_verse_end": verse_data["verse_end"],
                        "scripture_version": verse_data["version"],
                    }
                except ScriptureError as e:
                    raise PostError(
                        message=e.message,
                        code=ErrorCode.VALIDATION_ERROR,
                    ) from e

            combined_len = len(caption or "") + len(scripture_text)
            if combined_len > TEXT_POST_MAX_CAPTION:
                raise PostError(
                    message=f"Text payload cap is {TEXT_POST_MAX_CAPTION} chars (including scripture). You provided {combined_len}.",
                    code="TEXT_POST_TOO_LONG_WITH_SCRIPTURE"
                    if scripture_text
                    else ErrorCode.VALIDATION_ERROR,
                )
            if media_items:
                raise PostError(
                    message="Text posts cannot have media attachments.",
                    code=ErrorCode.VALIDATION_ERROR,
                )

        elif post_type == PostType.IMAGE:
            if not media_items:
                raise PostError(
                    message=f"Image posts require {IMAGE_MIN_COUNT}-{IMAGE_MAX_COUNT} images",
                    code=ErrorCode.IMAGE_COUNT_INVALID,
                )
            if len(media_items) < IMAGE_MIN_COUNT or len(media_items) > IMAGE_MAX_COUNT:
                raise PostError(
                    message=f"Image posts require {IMAGE_MIN_COUNT}-{IMAGE_MAX_COUNT} images",
                    code=ErrorCode.IMAGE_COUNT_INVALID,
                    extensions={
                        "min_count": IMAGE_MIN_COUNT,
                        "max_count": IMAGE_MAX_COUNT,
                        "actual_count": len(media_items),
                    },
                )
            for item in media_items:
                if item.get("media_type") != "image":
                    raise PostError(
                        message="Image posts can only contain image media",
                        code=ErrorCode.MEDIA_TYPE_MISMATCH,
                    )
            if caption and len(caption) > MEDIA_POST_MAX_CAPTION:
                raise PostError(
                    message=f"Caption limited to {MEDIA_POST_MAX_CAPTION} characters",
                    code=ErrorCode.TEXT_POST_TOO_LONG,
                )

        elif post_type == PostType.VIDEO:
            if len(media_items) != 1:
                raise PostError(
                    message="Video posts require exactly 1 video",
                    code=ErrorCode.IMAGE_COUNT_INVALID,
                )
            video = media_items[0]
            if video.get("media_type") != "video":
                raise PostError(
                    message="Video posts must contain video media",
                    code=ErrorCode.MEDIA_TYPE_MISMATCH,
                )
            duration = video.get("duration", 0)
            if duration < VIDEO_MIN_DURATION:
                raise PostError(
                    message=f"Video must be at least {VIDEO_MIN_DURATION} seconds",
                    code=ErrorCode.VIDEO_TOO_SHORT,
                    extensions={
                        "min_duration": VIDEO_MIN_DURATION,
                        "actual_duration": duration,
                    },
                )
            if duration > VIDEO_MAX_DURATION:
                raise PostError(
                    message=f"Video cannot exceed {VIDEO_MAX_DURATION} seconds",
                    code=ErrorCode.VIDEO_TOO_LONG,
                    extensions={
                        "max_duration": VIDEO_MAX_DURATION,
                        "actual_duration": duration,
                    },
                )
            if caption and len(caption) > MEDIA_POST_MAX_CAPTION:
                raise PostError(
                    message=f"Caption limited to {MEDIA_POST_MAX_CAPTION} characters",
                    code=ErrorCode.TEXT_POST_TOO_LONG,
                )
        else:
            if len(caption or "") > MEDIA_POST_MAX_CAPTION:
                raise PostError(
                    message=f"Caption exceeds {MEDIA_POST_MAX_CAPTION} items.",
                    code=ErrorCode.VALIDATION_ERROR,
                )

            if scripture_book and scripture_chapter and scripture_verse_start:
                from core.scripture.services import ScriptureError, ScriptureService

                try:
                    verse_data = ScriptureService.fetch_verse(
                        book=scripture_book,
                        chapter=scripture_chapter,
                        verse_start=scripture_verse_start,
                        verse_end=scripture_verse_end,
                        version=scripture_version,
                    )
                    scripture_fields = {
                        "scripture_book": verse_data["book"],
                        "scripture_chapter": verse_data["chapter"],
                        "scripture_verse_start": verse_data["verse_start"],
                        "scripture_verse_end": verse_data["verse_end"],
                        "scripture_version": verse_data["version"],
                    }
                except ScriptureError as e:
                    raise PostError(
                        message=e.message,
                        code=ErrorCode.VALIDATION_ERROR,
                    ) from e

        try:
            user = User.objects.get(id=user_id, deleted_at__isnull=True)
        except User.DoesNotExist:
            raise PostError(
                message="User not found",
                code=ErrorCode.USER_NOT_FOUND,
            ) from None

        post = Post.objects.create(
            user=user,
            post_type=post_type,
            caption=caption or "",
            category=category,
            media_count=len(media_items),
            **scripture_fields,
        )

        post_media_objects = []
        for idx, item in enumerate(media_items):
            post_media_objects.append(
                PostMedia(
                    post=post,
                    media_url=item["media_url"],
                    media_type=item["media_type"],
                    thumbnail_url=item.get("thumbnail_url", ""),
                    order=idx,
                    width=item.get("width", 0),
                    height=item.get("height", 0),
                    duration=item.get("duration"),
                )
            )
        if post_media_objects:
            PostMedia.objects.bulk_create(post_media_objects)

        try:
            from core.feed.tasks import invalidate_followers_feed_cache

            invalidate_followers_feed_cache.delay(str(user.id))
        except Exception:
            logger.warning("Failed to queue feed cache invalidation")

        logger.info(
            "post_created",
            extra={
                "post_id": str(post.id),
                "user_id": str(user.id),
                "post_type": post_type,
                "media_count": len(media_items),
            },
        )

        return PostService._build_post_dto(
            post,
            list(post.post_media.all()),
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

        media_items = list(post.post_media.all())
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

        media_items = list(post.post_media.all())
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
        media_items: list[PostMedia],
        viewer_id: str | None = None,
        is_owner: bool | None = None,
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

        if post.post_type == PostType.IMAGE:
            media = ImageMediaDTO(
                items=[
                    MediaItemDTO(
                        id=str(m.id),
                        url=m.media_url,
                        width=m.width,
                        height=m.height,
                        order=m.order,
                    )
                    for m in sorted(media_items, key=lambda x: x.order)
                ],
            )
        elif post.post_type == PostType.VIDEO and media_items:
            v = media_items[0]
            media = VideoMediaDTO(
                url=v.media_url,
                thumbnail_url=v.thumbnail_url or "",
                duration=v.duration or 0,
                width=v.width,
                height=v.height,
            )
        else:
            media = TextMediaDTO()

        stats = StatsDTO(
            likes_count=getattr(post, "likes_count", 0) or post.likes.count()
            if not hasattr(post, "likes_count")
            else getattr(post, "likes_count", 0),
            comments_count=getattr(post, "comments_count", 0)
            or post.comments.filter(deleted_at__isnull=True).count()
            if not hasattr(post, "comments_count")
            else getattr(post, "comments_count", 0),
            shares_count=getattr(post, "shares_count", 0) or post.shares.count()
            if not hasattr(post, "shares_count")
            else getattr(post, "shares_count", 0),
            saves_count=getattr(post, "saves_count", 0) or post.saves.count()
            if not hasattr(post, "saves_count")
            else getattr(post, "saves_count", 0),
        )

        viewer_state = None
        if viewer_id:
            viewer_state = ViewerStateDTO(
                liked=Like.objects.filter(user_id=viewer_id, post_id=post.id).exists(),
                saved=Save.objects.filter(user_id=viewer_id, post_id=post.id).exists(),
                following_author=Follow.objects.filter(
                    follower_id=viewer_id, following_id=post.user_id
                ).exists()
                if str(post.user_id) != viewer_id
                else False,
                is_owner=is_owner if is_owner is not None else (str(post.user_id) == viewer_id),
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
                )

        return PostResponseDTO(
            id=str(post.id),
            type=post.post_type,
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

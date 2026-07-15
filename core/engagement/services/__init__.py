"""Engagement services package.

EngagementService keeps its full public method surface; comment/bookmark/hidden
implementations live in sibling modules and are attached as staticmethods.
The likes methods stay in this module so the test patch target
`core.engagement.services.check_engagement_spam` keeps intercepting them.
"""
import logging
import re

from django.db import IntegrityError

from core.engagement.models import (
    Like,
)
from core.posts.models import Post
from core.shared.decorators import rate_limit
from core.shared.dtos import (
    LikeResponseDTO,
)
from core.shared.exceptions import EngagementError, ErrorCode
from core.shared.spam import check_engagement_spam

logger = logging.getLogger("core.engagement")

COMMENT_MAX_LENGTH = 500
COMMENT_MAX_THREAD_DEPTH = 3
MENTION_REGEX = re.compile(r"@(\w{3,30})")

DEFAULT_BOOKMARK_FOLDERS = [
    "All",
]


from core.engagement.services import bookmarks, comments, hidden  # noqa: E402


class EngagementService:
    """Service class for all engagement operations."""

    @staticmethod
    @rate_limit(max_requests=30, window_seconds=60)
    def like_post(user_id: str, post_id: str) -> LikeResponseDTO:
        """Like a post.

        Args:
            user_id: UUID of the user liking the post.
            post_id: UUID of the post to like.

        Returns:
            LikeResponseDTO with success status.

        Raises:
            EngagementError: If post not found or already liked.
        """
        post = Post.objects.filter(id=post_id, deleted_at__isnull=True).first()
        if not post:
            raise EngagementError(
                message="Post not found.",
                code=ErrorCode.POST_NOT_FOUND,
            )

        check_engagement_spam(user_id, post_id, action="like")

        try:
            _like, created = Like.objects.get_or_create(user_id=user_id, post_id=post_id)
            if not created:
                return LikeResponseDTO(success=True, liked=True, likes_count=post.likes.count())
            logger.info(
                "post_liked",
                extra={"user_id": user_id, "post_id": post_id},
            )
            return LikeResponseDTO(success=True, liked=True, likes_count=post.likes.count())
        except IntegrityError:
            logger.info(
                "post_like_already_exists",
                extra={"user_id": user_id, "post_id": post_id},
            )
            return LikeResponseDTO(success=True, liked=True, likes_count=post.likes.count())

    @staticmethod
    def ensure_post_liked(user_id: str, post_id: str) -> LikeResponseDTO:
        """Idempotently ensure a post is liked.

        Intended for double-tap UI gestures where repeated calls should never
        toggle the like off or return an already-liked error.
        """
        return EngagementService.like_post(user_id, post_id)

    @staticmethod
    @rate_limit(max_requests=30, window_seconds=60)
    def unlike_post(user_id: str, post_id: str) -> LikeResponseDTO:
        """Unlike a post.

        Args:
            user_id: UUID of the user unliking the post.
            post_id: UUID of the post to unlike.

        Returns:
            LikeResponseDTO with success status.
        """
        check_engagement_spam(user_id, post_id, action="unlike")

        deleted_count, _ = Like.objects.filter(user_id=user_id, post_id=post_id).delete()

        if deleted_count:
            logger.info(
                "post_unliked",
                extra={"user_id": user_id, "post_id": post_id},
            )

        return LikeResponseDTO(success=True, liked=False)

    # Implementations split into sibling modules; surface unchanged.
    create_comment = staticmethod(comments.create_comment)
    delete_comment = staticmethod(comments.delete_comment)
    like_comment = staticmethod(comments.like_comment)
    unlike_comment = staticmethod(comments.unlike_comment)
    get_post_comments = staticmethod(comments.get_post_comments)
    get_comment_replies = staticmethod(comments.get_comment_replies)
    _get_thread_depth = staticmethod(comments._get_thread_depth)
    _parse_mentions = staticmethod(comments._parse_mentions)
    _build_comment_dto = staticmethod(comments._build_comment_dto)
    save_post = staticmethod(bookmarks.save_post)
    unsave_post = staticmethod(bookmarks.unsave_post)
    _ensure_default_folders = staticmethod(bookmarks._ensure_default_folders)
    hide_post = staticmethod(hidden.hide_post)
    unhide_post = staticmethod(hidden.unhide_post)
    get_hidden_posts = staticmethod(hidden.get_hidden_posts)

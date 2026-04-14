"""
Engagement service — business logic for likes, comments, saves, and comment likes.

Handles spam detection, threading depth limits, @mention parsing,
and auto-creation of default bookmark folders.
"""

import logging
import re

from django.db import IntegrityError
from django.db.models import Count, Q

from core.engagement.models import (
    BookmarkFolder,
    Comment,
    CommentLike,
    Like,
    Save,
)
from core.posts.models import Post
from core.shared.decorators import rate_limit
from core.shared.dtos import (
    AuthorDTO,
    CommentResponseDTO,
    CommentsResponseDTO,
    CommentStatsDTO,
    CommentViewerStateDTO,
    LikeResponseDTO,
    SaveResponseDTO,
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


class EngagementService:
    """Service handling engagement operations: likes, comments, saves."""

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
            Like.objects.create(user_id=user_id, post_id=post_id)
            logger.info(
                "post_liked",
                extra={"user_id": user_id, "post_id": post_id},
            )
            return LikeResponseDTO(success=True, liked=True)
        except IntegrityError as e:
            raise EngagementError(
                message="You have already liked this post.",
                code=ErrorCode.ALREADY_LIKED,
            ) from e

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

    @staticmethod
    @rate_limit(max_requests=10, window_seconds=60)
    def create_comment(
        user_id: str,
        post_id: str,
        text: str,
        parent_comment_id: str | None = None,
    ) -> CommentResponseDTO:
        """Create a comment on a post.

        Args:
            user_id: UUID of the comment author.
            post_id: UUID of the post.
            text: Comment text content.
            parent_comment_id: Optional parent for threaded replies.

        Returns:
            CommentResponseDTO for the new comment.

        Raises:
            EngagementError: If validation fails.
        """
        if not text or not text.strip():
            raise EngagementError(
                message="Comment text is required.",
                code=ErrorCode.VALIDATION_ERROR,
            )

        if len(text) > COMMENT_MAX_LENGTH:
            raise EngagementError(
                message=f"Comment exceeds maximum length of {COMMENT_MAX_LENGTH} characters.",
                code=ErrorCode.COMMENT_TOO_LONG,
            )

        post = Post.objects.filter(id=post_id, deleted_at__isnull=True).first()
        if not post:
            raise EngagementError(
                message="Post not found.",
                code=ErrorCode.POST_NOT_FOUND,
            )

        parent_comment = None
        if parent_comment_id:
            parent_comment = Comment.objects.filter(
                id=parent_comment_id, deleted_at__isnull=True
            ).first()

            if not parent_comment:
                raise EngagementError(
                    message="Parent comment not found.",
                    code=ErrorCode.COMMENT_NOT_FOUND,
                )

            if str(parent_comment.post_id).lower() != str(post_id).lower():
                raise EngagementError(
                    message="Parent comment does not belong to this post.",
                    code=ErrorCode.COMMENT_POST_MISMATCH,
                )

            depth = EngagementService._get_thread_depth(parent_comment)
            if depth >= COMMENT_MAX_THREAD_DEPTH:
                raise EngagementError(
                    message=f"Maximum thread depth of {COMMENT_MAX_THREAD_DEPTH} reached.",
                    code=ErrorCode.COMMENT_THREAD_TOO_DEEP,
                )

        mentioned_users = EngagementService._parse_mentions(text)

        comment = Comment.objects.create(
            post_id=post_id,
            user_id=user_id,
            parent_comment=parent_comment,
            text=text.strip(),
            mentioned_users=mentioned_users,
        )

        comment = Comment.objects.select_related("user").filter(id=comment.id).first()

        logger.info(
            "comment_created",
            extra={
                "user_id": user_id,
                "post_id": post_id,
                "comment_id": str(comment.id),
                "is_reply": parent_comment_id is not None,
            },
        )

        return EngagementService._build_comment_dto(comment, viewer_id=user_id)

    @staticmethod
    def delete_comment(user_id: str, comment_id: str) -> bool:
        """Soft-delete a comment.

        Args:
            user_id: UUID of the requesting user.
            comment_id: UUID of the comment to delete.

        Returns:
            True if successfully deleted.

        Raises:
            EngagementError: If comment not found or permission denied.
        """
        comment = Comment.objects.filter(id=comment_id, deleted_at__isnull=True).first()

        if not comment:
            raise EngagementError(
                message="Comment not found.",
                code=ErrorCode.COMMENT_NOT_FOUND,
            )

        if str(comment.user_id) != str(user_id):
            raise EngagementError(
                message="You can only delete your own comments.",
                code=ErrorCode.PERMISSION_DENIED,
            )

        comment.soft_delete()

        logger.info(
            "comment_deleted",
            extra={"user_id": user_id, "comment_id": comment_id},
        )

        return True

    @staticmethod
    @rate_limit(max_requests=30, window_seconds=60)
    def like_comment(user_id: str, comment_id: str) -> bool:
        """Like a comment.

        Args:
            user_id: UUID of the user.
            comment_id: UUID of the comment.

        Returns:
            True if successfully liked.

        Raises:
            EngagementError: If comment not found.
        """
        comment = Comment.objects.filter(id=comment_id, deleted_at__isnull=True).first()

        if not comment:
            raise EngagementError(
                message="Comment not found.",
                code=ErrorCode.COMMENT_NOT_FOUND,
            )

        try:
            CommentLike.objects.create(user_id=user_id, comment_id=comment_id)
            return True
        except IntegrityError:
            return True

    @staticmethod
    def unlike_comment(user_id: str, comment_id: str) -> bool:
        """Unlike a comment.

        Args:
            user_id: UUID of the user.
            comment_id: UUID of the comment.

        Returns:
            True if successfully unliked.
        """
        CommentLike.objects.filter(user_id=user_id, comment_id=comment_id).delete()
        return True

    @staticmethod
    def get_post_comments(
        post_id: str,
        viewer_id: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> CommentsResponseDTO:
        """Get paginated comments for a post.

        Args:
            post_id: UUID of the post.
            viewer_id: Optional viewer for personalized state.
            cursor: Comment ID for cursor pagination.
            limit: Page size (max 50).

        Returns:
            CommentsResponseDTO with paginated comments.
        """
        limit = min(limit, 50)

        qs = (
            Comment.objects.select_related("user")
            .filter(
                post_id=post_id,
                deleted_at__isnull=True,
                parent_comment__isnull=True,
            )
            .annotate(
                likes_count=Count("comment_likes", distinct=True),
                replies_count=Count(
                    "replies",
                    filter=Q(replies__deleted_at__isnull=True),
                    distinct=True,
                ),
            )
            .order_by("-created_at")
        )

        if cursor:
            try:
                cursor_comment = Comment.objects.filter(id=cursor).values("created_at").first()
                if cursor_comment:
                    qs = qs.filter(created_at__lt=cursor_comment["created_at"])
            except Exception:  # noqa: S110
                pass

        total_count = qs.count()
        comments = list(qs[: limit + 1])
        has_more = len(comments) > limit
        comments = comments[:limit]

        comment_dtos = [
            EngagementService._build_comment_dto(c, viewer_id=viewer_id) for c in comments
        ]

        next_cursor = str(comments[-1].id) if has_more and comments else None

        return CommentsResponseDTO(
            comments=comment_dtos,
            next_cursor=next_cursor,
            has_more=has_more,
            total_count=total_count,
        )

    @staticmethod
    def get_comment_replies(
        comment_id: str,
        viewer_id: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> CommentsResponseDTO:
        """Get paginated replies for a specific comment.

        Args:
            comment_id: UUID of the parent comment.
            viewer_id: Optional viewer for personalised viewer_state.
            cursor: Comment ID for cursor pagination.
            limit: Page size (max 50).

        Returns:
            CommentsResponseDTO with paginated reply comments, oldest-first.
        """
        limit = min(limit, 50)

        qs = (
            Comment.objects.select_related("user")
            .filter(
                parent_comment_id=comment_id,
                deleted_at__isnull=True,
            )
            .annotate(
                likes_count=Count("comment_likes", distinct=True),
                replies_count=Count(
                    "replies",
                    filter=Q(replies__deleted_at__isnull=True),
                    distinct=True,
                ),
            )
            .order_by("created_at")  # Oldest-first — chronological thread order
        )

        if cursor:
            try:
                cursor_comment = Comment.objects.filter(id=cursor).values("created_at").first()
                if cursor_comment:
                    qs = qs.filter(created_at__gt=cursor_comment["created_at"])
            except Exception:  # noqa: S110
                pass

        total_count = qs.count()
        replies = list(qs[: limit + 1])
        has_more = len(replies) > limit
        replies = replies[:limit]

        reply_dtos = [
            EngagementService._build_comment_dto(r, viewer_id=viewer_id, include_replies=False)
            for r in replies
        ]

        next_cursor = str(replies[-1].id) if has_more and replies else None

        return CommentsResponseDTO(
            comments=reply_dtos,
            next_cursor=next_cursor,
            has_more=has_more,
            total_count=total_count,
        )

    @staticmethod
    @rate_limit(max_requests=30, window_seconds=60)
    def save_post(
        user_id: str,
        post_id: str,
        folder_id: str | None = None,
    ) -> SaveResponseDTO:
        """Save/bookmark a post.

        Auto-creates default folders if the user doesn't have any yet.

        Args:
            user_id: UUID of the user.
            post_id: UUID of the post.
            folder_id: Optional folder to save into.

        Returns:
            SaveResponseDTO with success status.

        Raises:
            EngagementError: If post not found or already saved.
        """
        post = Post.objects.filter(id=post_id, deleted_at__isnull=True).first()
        if not post:
            raise EngagementError(
                message="Post not found.",
                code=ErrorCode.POST_NOT_FOUND,
            )

        EngagementService._ensure_default_folders(user_id)

        folder = None
        if folder_id:
            folder = BookmarkFolder.objects.filter(id=folder_id, user_id=user_id).first()
            if not folder:
                raise EngagementError(
                    message="Bookmark folder not found.",
                    code=ErrorCode.FOLDER_NOT_FOUND,
                )

        try:
            Save.objects.create(
                user_id=user_id,
                post_id=post_id,
                folder=folder,
            )
            logger.info(
                "post_saved",
                extra={"user_id": user_id, "post_id": post_id},
            )
            return SaveResponseDTO(success=True, saved=True)
        except IntegrityError as e:
            raise EngagementError(
                message="You have already saved this post.",
                code=ErrorCode.ALREADY_SAVED,
            ) from e

    @staticmethod
    def unsave_post(user_id: str, post_id: str) -> SaveResponseDTO:
        """Remove a saved post.

        Args:
            user_id: UUID of the user.
            post_id: UUID of the post.

        Returns:
            SaveResponseDTO with success status.
        """
        deleted_count, _ = Save.objects.filter(user_id=user_id, post_id=post_id).delete()

        if deleted_count:
            logger.info(
                "post_unsaved",
                extra={"user_id": user_id, "post_id": post_id},
            )

        return SaveResponseDTO(success=True, saved=False)

    @staticmethod
    def _get_thread_depth(comment: Comment) -> int:
        """Calculate the depth of a comment in its thread."""
        depth = 0
        current = comment
        while current.parent_comment_id is not None:
            depth += 1
            if depth >= COMMENT_MAX_THREAD_DEPTH:
                break
            current = Comment.objects.filter(id=current.parent_comment_id).first()
            if not current:
                break
        return depth

    @staticmethod
    def _parse_mentions(text: str) -> list[str]:
        """Extract @mentioned usernames from comment text."""
        from core.users.models import User

        usernames = MENTION_REGEX.findall(text)
        if not usernames:
            return []

        existing = User.objects.filter(username__in=usernames, deleted_at__isnull=True).values_list(
            "id", flat=True
        )

        return [str(uid) for uid in existing]

    @staticmethod
    def _ensure_default_folders(user_id: str) -> None:
        """Create default bookmark folders if user has none."""
        if BookmarkFolder.objects.filter(user_id=user_id).exists():
            return

        folders = [BookmarkFolder(user_id=user_id, name=name) for name in DEFAULT_BOOKMARK_FOLDERS]
        BookmarkFolder.objects.bulk_create(folders, ignore_conflicts=True)

    @staticmethod
    def _build_comment_dto(
        comment: Comment,
        viewer_id: str | None = None,
        include_replies: bool = True,
    ) -> CommentResponseDTO:
        """Build a CommentResponseDTO from a Comment instance.

        Args:
            comment: The Comment model instance.
            viewer_id: Optional viewer for personalised viewer_state.
            include_replies: If True, attaches first 3 replies as a preview.
                             Must be False when recursively building reply DTOs
                             to prevent infinite nesting.
        """
        likes_count = getattr(comment, "likes_count", None)
        if likes_count is None:
            likes_count = comment.comment_likes.count()

        replies_count = getattr(comment, "replies_count", None)
        if replies_count is None:
            replies_count = comment.replies.filter(deleted_at__isnull=True).count()

        stats = CommentStatsDTO(
            likes_count=likes_count,
            replies_count=replies_count,
        )

        viewer_state = None
        if viewer_id:
            liked = CommentLike.objects.filter(user_id=viewer_id, comment_id=comment.id).exists()
            viewer_state = CommentViewerStateDTO(
                liked=liked,
                is_owner=str(comment.user_id) == str(viewer_id),
            )

        # Attach a small inline preview of the first 3 replies.
        # Replies are built with include_replies=False to stop recursion at one level.
        reply_previews = []
        if include_replies and replies_count > 0:
            preview_qs = (
                Comment.objects.select_related("user")
                .filter(parent_comment_id=comment.id, deleted_at__isnull=True)
                .annotate(
                    likes_count=Count("comment_likes", distinct=True),
                    replies_count=Count(
                        "replies",
                        filter=Q(replies__deleted_at__isnull=True),
                        distinct=True,
                    ),
                )
                .order_by("created_at")[:3]
            )
            reply_previews = [
                EngagementService._build_comment_dto(r, viewer_id=viewer_id, include_replies=False)
                for r in preview_qs
            ]

        return CommentResponseDTO(
            id=str(comment.id),
            post_id=str(comment.post_id),
            parent_comment_id=(
                str(comment.parent_comment_id) if comment.parent_comment_id else None
            ),
            user=AuthorDTO(
                id=str(comment.user.id),
                username=comment.user.username or "",
                avatar_url=comment.user.avatar_url or None,
            ),
            text=comment.text,
            stats=stats,
            viewer_state=viewer_state,
            created_at=comment.created_at.isoformat(),
            replies=reply_previews,
        )

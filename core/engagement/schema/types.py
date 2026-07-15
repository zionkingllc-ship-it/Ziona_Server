"""Engagement GraphQL types + DTO mappers.

Split from the former core/engagement/schema.py (no contract change).
"""

from __future__ import annotations

import logging

import strawberry

from core.feed.schema import FeedPost
from core.posts.schema import PostStats
from core.shared.types import ErrorType

logger = logging.getLogger("core.engagement")


@strawberry.type
class LikePayload:
    """Response for like/unlike mutations."""

    success: bool
    liked: bool = False
    stats: PostStats | None = None
    comment_stats: CommentStats | None = None
    error: ErrorType | None = None
    message: str | None = None
    error_code: str | None = None


@strawberry.type
class SavePayload:
    """Response for save/unsave mutations."""

    success: bool
    saved: bool = False
    folder: BookmarkFolderType | None = None
    post: FeedPost | None = None
    stats: PostStats | None = None
    error: ErrorType | None = None
    message: str | None = None
    error_code: str | None = None


@strawberry.type
class CommentAuthor:
    """Author info within a comment."""

    id: str
    username: str
    avatar_url: str | None = None


@strawberry.type
class CommentStats:
    """Stats for a comment."""

    likes_count: int = 0
    replies_count: int = 0


@strawberry.type
class CommentViewerState:
    """Viewer state for a comment."""

    liked: bool = False
    is_owner: bool = False


@strawberry.type
class CommentType:
    """A comment on a post, with an inline preview of the first 3 replies."""

    id: str
    post_id: str
    parent_comment_id: str | None = None
    user: CommentAuthor
    text: str
    stats: CommentStats
    viewer_state: CommentViewerState | None = None
    created_at: str
    # Inline reply preview: first 3 replies.
    # Use commentReplies(commentId: ...) query to load more.
    replies: list[CommentType] = strawberry.field(default_factory=list)


@strawberry.type
class CommentPayload:
    """Response for comment mutations."""

    success: bool
    comment: CommentType | None = None
    stats: PostStats | None = None
    error: ErrorType | None = None
    message: str | None = None
    error_code: str | None = None


@strawberry.type
class CommentsResponse:
    """Paginated comments response."""

    comments: list[CommentType]
    next_cursor: str | None = None
    has_more: bool = False
    total_count: int = 0


@strawberry.type
class BookmarkFolderType:
    """A bookmark folder."""

    id: str
    name: str
    saved_count: int = 0
    created_at: str
    thumbnail_url: str | None = None


@strawberry.type
class BookmarkFolderPayload:
    """Response for folder mutations."""

    success: bool
    folder: BookmarkFolderType | None = None
    error: ErrorType | None = None
    message: str | None = None
    error_code: str | None = None


@strawberry.type
class DeleteFolderPayload:
    """Response for folder deletion."""

    success: bool
    error: ErrorType | None = None
    message: str | None = None
    moved_posts_count: int = 0
    error_code: str | None = None


@strawberry.type
class BulkRemovePayload:
    """Response for bulk bookmark removal."""

    success: bool
    removed_count: int = 0
    error: ErrorType | None = None
    message: str | None = None
    error_code: str | None = None


@strawberry.type
class HidePostPayload:
    """Response for hiding/unhiding a post."""

    success: bool
    error: ErrorType | None = None
    message: str | None = None
    error_code: str | None = None


@strawberry.type
class HiddenPostsResponse:
    """Paginated list of hidden posts."""

    posts: list[FeedPost]
    next_cursor: str | None = None
    has_more: bool = False


@strawberry.type
class SavedPostsResponse:
    """Paginated saved posts response."""

    posts: list[FeedPost]
    next_cursor: str | None = None
    has_more: bool = False


@strawberry.type
class SharePayload:
    """Response for share mutations."""

    success: bool
    share_id: str | None = None
    share_url: str | None = None
    stats: PostStats | None = None
    error: ErrorType | None = None
    message: str | None = None
    error_code: str | None = None


@strawberry.type
class FriendType:
    """A friend (mutual follow) for the share picker."""

    id: str
    username: str
    avatar_url: str | None = None
    full_name: str = ""


def _dto_to_comment(dto) -> CommentType:
    """Convert CommentResponseDTO to CommentType."""
    return CommentType(
        id=dto.id,
        post_id=dto.post_id,
        parent_comment_id=dto.parent_comment_id,
        user=CommentAuthor(
            id=dto.user.id,
            username=dto.user.username,
            avatar_url=dto.user.avatar_url,
        ),
        text=dto.text,
        stats=CommentStats(
            likes_count=dto.stats.likes_count,
            replies_count=dto.stats.replies_count,
        ),
        viewer_state=(
            CommentViewerState(
                liked=dto.viewer_state.liked,
                is_owner=dto.viewer_state.is_owner,
            )
            if dto.viewer_state
            else None
        ),
        created_at=dto.created_at,
        # Recursively convert inline reply previews (at most 1 level deep)
        replies=[_dto_to_comment(r) for r in getattr(dto, "replies", [])],
    )


def _dto_to_post_stats(dto) -> PostStats:
    """Convert a post DTO into canonical GraphQL post stats."""
    return PostStats(
        likes_count=dto.stats.likes_count,
        comments_count=dto.stats.comments_count,
        shares_count=dto.stats.shares_count,
        saves_count=dto.stats.saves_count,
    )

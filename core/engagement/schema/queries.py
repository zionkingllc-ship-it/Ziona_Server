"""Engagement GraphQL queries.

Split from the former core/engagement/schema.py (no contract change).
"""

from __future__ import annotations

import logging

import strawberry

from core.feed.schema import _dto_to_feed_post
from core.users.schema import _get_authenticated_user_id

logger = logging.getLogger("core.engagement")

from core.engagement.schema.types import (  # noqa: E402,F401
    BookmarkFolderPayload,
    BookmarkFolderType,
    BulkRemovePayload,
    CommentPayload,
    CommentsResponse,
    CommentType,
    DeleteFolderPayload,
    FriendType,
    HiddenPostsResponse,
    HidePostPayload,
    LikePayload,
    SavedPostsResponse,
    SavePayload,
    SharePayload,
    _dto_to_comment,
    _dto_to_post_stats,
)


@strawberry.type
class EngagementQueries:
    """Engagement domain GraphQL queries."""

    @strawberry.field(
        description="Get hierarchical chronological array of comments bounded to an entity."
    )
    def post_comments(
        self,
        info: strawberry.types.Info,
        post_id: str,
        cursor: str | None = None,
        limit: int = 20,
    ) -> CommentsResponse:
        """
        Get paginated top-level comments for a post, each with an inline
        preview of the first 3 replies.

        Use ``commentReplies(commentId: ...)`` to load additional replies
        beyond the preview.

        **Authentication:** Optional
        **Parameters:**
        - post_id (String, required) - Post UUID
        - cursor (String, optional) - Continuation token
        - limit (Int, optional) - Page size (default 20, max 50)
        **Returns:** CommentsResponse with paginated top-level comments
        """
        from core.engagement.services import EngagementService

        user_id = _get_authenticated_user_id(info)

        result = EngagementService.get_post_comments(
            post_id=post_id,
            viewer_id=user_id,
            cursor=cursor,
            limit=limit,
        )

        return CommentsResponse(
            comments=[_dto_to_comment(c) for c in result.comments],
            next_cursor=result.next_cursor,
            has_more=result.has_more,
            total_count=result.total_count,
        )

    @strawberry.field(
        description="Get paginated replies for a specific comment (beyond the inline 3-reply preview)."
    )
    def comment_replies(
        self,
        info: strawberry.types.Info,
        comment_id: str,
        cursor: str | None = None,
        limit: int = 20,
    ) -> CommentsResponse:
        """
        Fetch the full paginated reply thread for a parent comment.

        The ``postComments`` query already returns the first 3 replies inline.
        Call this query when the user taps "View N more replies".

        **Authentication:** Optional
        **Parameters:**
        - comment_id (String, required) - Parent comment UUID
        - cursor (String, optional) - Reply ID continuation token
        - limit (Int, optional) - Page size (default 20, max 50)
        **Returns:** CommentsResponse with reply comments (oldest-first)
        """
        from core.engagement.services import EngagementService

        user_id = _get_authenticated_user_id(info)

        result = EngagementService.get_comment_replies(
            comment_id=comment_id,
            viewer_id=user_id,
            cursor=cursor,
            limit=limit,
        )

        return CommentsResponse(
            comments=[_dto_to_comment(c) for c in result.comments],
            next_cursor=result.next_cursor,
            has_more=result.has_more,
            total_count=result.total_count,
        )

    @strawberry.field(description="Get bookmark folders")
    def bookmark_folders(self, info: strawberry.types.Info) -> list[BookmarkFolderType]:
        """Get all bookmark folders for the authenticated user."""
        from core.engagement.bookmark_services import BookmarkService

        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return []

        folders = BookmarkService.get_folders(user_id)
        return [
            BookmarkFolderType(
                id=f.id,
                name=f.name,
                saved_count=f.saved_count,
                created_at=f.created_at,
                thumbnail_url=f.thumbnail_url,
            )
            for f in folders
        ]

    @strawberry.field(description="Get friends list for sharing")
    def friends_list(
        self,
        info: strawberry.types.Info,
        search: str | None = None,
        limit: int = 20,
    ) -> list[FriendType]:
        """Get friends (mutual follows) for the share picker."""
        from core.engagement.share_services import ShareService

        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return []

        friends = ShareService.get_friends_list(user_id, search, limit)
        return [
            FriendType(
                id=f["user"].id,
                username=f["user"].username,
                avatar_url=f["user"].avatar_url,
                full_name=f.get("full_name", ""),
            )
            for f in friends
        ]

    @strawberry.field(description="Get saved/bookmarked posts")
    def saved_posts(
        self,
        info: strawberry.types.Info,
        folder_id: str | None = None,
        media_type: str = "all",
        cursor: str | None = None,
        limit: int = 20,
    ) -> SavedPostsResponse:
        """Get saved posts with optional folder and media type filtering."""
        from core.engagement.bookmark_services import BookmarkService
        from core.shared.exceptions import BookmarkError

        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return SavedPostsResponse(posts=[], has_more=False)

        try:
            result = BookmarkService.get_saved_posts(
                user_id=user_id,
                folder_id=folder_id,
                media_type=media_type,
                cursor=cursor,
                limit=limit,
            )
            return SavedPostsResponse(
                posts=[_dto_to_feed_post(p) for p in result["posts"]],
                next_cursor=result["next_cursor"],
                has_more=result["has_more"],
            )
        except BookmarkError:
            return SavedPostsResponse(posts=[], has_more=False)

    @strawberry.field(description="Get paginated list of hidden posts")
    def hidden_posts(
        self,
        info: strawberry.types.Info,
        cursor: str | None = None,
        limit: int = 20,
    ) -> HiddenPostsResponse:
        """Get paginated list of hidden posts for a user."""
        from core.engagement.services import EngagementService
        from core.feed.services import FeedService

        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return HiddenPostsResponse(posts=[], has_more=False)

        posts, next_cursor, has_more = EngagementService.get_hidden_posts(user_id, cursor, limit)
        dtos = FeedService._bulk_build_post_dtos(posts, viewer_id=user_id)

        return HiddenPostsResponse(
            posts=[_dto_to_feed_post(p) for p in dtos],
            next_cursor=next_cursor,
            has_more=has_more,
        )

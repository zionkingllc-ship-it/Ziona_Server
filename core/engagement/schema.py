"""GraphQL types, queries, and mutations for the engagement domain."""

from __future__ import annotations

import logging

import strawberry

from core.feed.schema import FeedPost, _dto_to_feed_post
from core.posts.schema import PostStats
from core.shared.types import ErrorType
from core.users.schema import _get_authenticated_user_id

logger = logging.getLogger("core.engagement")


@strawberry.type
class LikePayload:
    """Response for like/unlike mutations."""

    success: bool
    liked: bool = False
    stats: PostStats | None = None
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


@strawberry.type
class EngagementMutations:
    """Engagement domain GraphQL mutations."""

    @strawberry.mutation(description="Optimistically toggle a 'like' on a specific post.")
    def like_post(self, info: strawberry.types.Info, post_id: str) -> LikePayload:
        """
        Like a post globally tracking metrics.

        Is idempotent so double liking just succeeds. Impacts Discovery algorithms implicitly.

        **Authentication:** Required
        **Parameters:**
        - post_id (String, required) - Valid remote UUID
        **Returns:** LikePayload mapping boolean state
        **Errors:** UNAUTHENTICATED, NOT_FOUND
        """
        from core.engagement.services import EngagementService
        from core.posts.services import PostService
        from core.shared.exceptions import EngagementError

        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return LikePayload(
                success=False,
                message="Authentication required",
                error_code="UNAUTHORIZED",
            )

        try:
            result = EngagementService.like_post(user_id, post_id)
            from core.posts.services import PostService

            p = PostService.get_post(post_id, user_id)
            stats = PostStats(
                likes_count=p.stats.likes_count,
                comments_count=p.stats.comments_count,
                shares_count=p.stats.shares_count,
                saves_count=p.stats.saves_count,
            )
            return LikePayload(success=True, liked=result.liked, stats=stats)
        except EngagementError as e:
            return LikePayload(
                success=False,
                message=e.message,
                error_code=e.code,
                error=ErrorType(code=e.code, message=e.message),
            )

    @strawberry.mutation(
        name="ensurePostLiked",
        description="Idempotently like a post. Repeated calls keep it liked.",
    )
    def ensure_post_liked(self, info: strawberry.types.Info, post_id: str) -> LikePayload:
        """Like a post without toggling or failing when it is already liked."""
        from core.engagement.services import EngagementService
        from core.shared.exceptions import EngagementError

        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return LikePayload(
                success=False,
                message="Authentication required",
                error_code="UNAUTHORIZED",
            )

        try:
            result = EngagementService.ensure_post_liked(user_id, post_id)
            from core.posts.services import PostService

            p = PostService.get_post(post_id, user_id)
            stats = PostStats(
                likes_count=p.stats.likes_count,
                comments_count=p.stats.comments_count,
                shares_count=p.stats.shares_count,
                saves_count=p.stats.saves_count,
            )
            return LikePayload(success=True, liked=result.liked, stats=stats)
        except EngagementError as e:
            return LikePayload(
                success=False,
                message=e.message,
                error_code=e.code,
                error=ErrorType(code=e.code, message=e.message),
            )

    @strawberry.mutation(description="Unlike a post")
    def unlike_post(self, info: strawberry.types.Info, post_id: str) -> LikePayload:
        """Unlike a post."""
        from core.engagement.services import EngagementService
        from core.shared.exceptions import EngagementError

        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return LikePayload(
                success=False,
                message="Authentication required",
                error_code="UNAUTHORIZED",
            )

        try:
            result = EngagementService.unlike_post(user_id, post_id)
            from core.posts.services import PostService

            p = PostService.get_post(post_id, user_id)
            stats = PostStats(
                likes_count=p.stats.likes_count,
                comments_count=p.stats.comments_count,
                shares_count=p.stats.shares_count,
                saves_count=p.stats.saves_count,
            )
            return LikePayload(success=True, liked=result.liked, stats=stats)
        except EngagementError as e:
            return LikePayload(
                success=False,
                message=e.message,
                error_code=e.code,
                error=ErrorType(code=e.code, message=e.message),
            )

    @strawberry.mutation(description="Create a nested or top-level text comment on a Post payload.")
    def create_comment(
        self,
        info: strawberry.types.Info,
        post_id: str,
        text: str,
        parent_comment_id: str | None = None,
    ) -> CommentPayload:
        """
        Create a new chronological comment on a post globally.

        Supports 1 level deep threading. Content moderation filters scan strings natively before insert.

        **Authentication:** Required
        **Parameters:**
        - post_id (String, required) - Active Post UUID target
        - text (String, required) - Comment body
        - parent_comment_id (String, optional) - Pass for replies
        **Returns:** CommentPayload extracting nested CommentType exactly
        **Errors:** UNAUTHENTICATED, VALIDATION_ERROR native limits.
        """
        from core.engagement.services import EngagementService
        from core.posts.services import PostService
        from core.shared.exceptions import EngagementError

        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return CommentPayload(
                success=False,
                message="Authentication required.",
                error_code="UNAUTHORIZED",
                error=ErrorType(code="UNAUTHORIZED", message="Authentication required."),
            )

        try:
            result = EngagementService.create_comment(
                user_id=user_id,
                post_id=post_id,
                text=text,
                parent_comment_id=parent_comment_id,
            )
            post = PostService.get_post(post_id, user_id)
            return CommentPayload(
                success=True,
                comment=_dto_to_comment(result),
                stats=_dto_to_post_stats(post),
            )
        except EngagementError as e:
            return CommentPayload(
                success=False,
                message=e.message,
                error_code=e.code,
                error=ErrorType(code=e.code, message=e.message),
            )
        except Exception as e:
            logger.exception("Unexpected error creating comment")
            return CommentPayload(
                success=False,
                message=str(e),
                error_code="INTERNAL_ERROR",
                error=ErrorType(code="INTERNAL_ERROR", message=str(e)),
            )

    @strawberry.mutation(description="Delete a comment")
    def delete_comment(self, info: strawberry.types.Info, comment_id: str) -> CommentPayload:
        """Delete a comment (soft delete)."""
        from core.engagement.services import EngagementService
        from core.posts.services import PostService
        from core.shared.exceptions import EngagementError

        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return CommentPayload(
                success=False,
                message="Authentication required",
                error_code="UNAUTHORIZED",
            )

        try:
            result = EngagementService.delete_comment(user_id, comment_id)
            post = PostService.get_post(result.post_id, user_id)
            return CommentPayload(success=True, stats=_dto_to_post_stats(post))
        except EngagementError as e:
            return CommentPayload(success=False, message=e.message, error_code=e.code)

    @strawberry.mutation(description="Like a comment")
    def like_comment(self, info: strawberry.types.Info, comment_id: str) -> LikePayload:
        """Like a comment."""
        from core.engagement.services import EngagementService
        from core.shared.exceptions import EngagementError

        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return LikePayload(
                success=False,
                message="Authentication required",
                error_code="UNAUTHORIZED",
            )

        try:
            EngagementService.like_comment(user_id, comment_id)
            return LikePayload(success=True, liked=True)
        except EngagementError as e:
            return LikePayload(success=False, message=e.message, error_code=e.code)

    @strawberry.mutation(description="Add bookmark saving a post strictly.")
    def save_post(
        self,
        info: strawberry.types.Info,
        post_id: str,
        folder_id: str | None = None,
        folder_name: str | None = None,
    ) -> SavePayload:
        """
        Save a post directly to a custom user Folder bookmark state natively.

        **Authentication:** Required
        **Parameters:**
        - post_id (String, required) - Active Post ID mapping
        - folder_id (String, optional) - Custom bounding string
        **Returns:** SavePayload Boolean tracking
        **Errors:** UNAUTHENTICATED, NOT_FOUND
        """
        from core.engagement.services import EngagementService
        from core.shared.exceptions import EngagementError

        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return SavePayload(
                success=False,
                message="Authentication required",
                error_code="UNAUTHORIZED",
            )

        try:
            result = EngagementService.save_post(user_id, post_id, folder_id, folder_name)

            # Map post natively
            post_obj = None
            if result.post:
                post_obj = _dto_to_feed_post(result.post)

            folder_obj = None
            if result.folder:
                folder_obj = BookmarkFolderType(
                    id=result.folder.id,
                    name=result.folder.name,
                    saved_count=result.folder.saved_count,
                    created_at=result.folder.created_at,
                    thumbnail_url=result.folder.thumbnail_url,
                )

            from core.posts.services import PostService

            p = PostService.get_post(post_id, user_id)
            stats = PostStats(
                likes_count=p.stats.likes_count,
                comments_count=p.stats.comments_count,
                shares_count=p.stats.shares_count,
                saves_count=p.stats.saves_count,
            )
            return SavePayload(
                success=True, saved=result.saved, stats=stats, folder=folder_obj, post=post_obj
            )
        except EngagementError as e:
            return SavePayload(
                success=False,
                message=e.message,
                error_code=e.code,
                error=ErrorType(code=e.code, message=e.message),
            )

    @strawberry.mutation(description="Unsave/remove a bookmark")
    def unsave_post(self, info: strawberry.types.Info, post_id: str) -> SavePayload:
        """Remove a saved post."""
        from core.engagement.services import EngagementService

        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return SavePayload(
                success=False,
                message="Authentication required",
                error_code="UNAUTHORIZED",
            )

        try:
            result = EngagementService.unsave_post(user_id, post_id)
            from core.posts.services import PostService

            p = PostService.get_post(post_id, user_id)
            stats = PostStats(
                likes_count=p.stats.likes_count,
                comments_count=p.stats.comments_count,
                shares_count=p.stats.shares_count,
                saves_count=p.stats.saves_count,
            )
            return SavePayload(success=True, saved=result.saved, stats=stats)
        except Exception as e:
            return SavePayload(
                success=False,
                message=str(e),
                error_code="UNSAVE_ERROR",
                error=ErrorType(code="UNSAVE_ERROR", message=str(e)),
            )

    @strawberry.mutation(description="Create a bookmark folder")
    def create_bookmark_folder(
        self,
        info: strawberry.types.Info,
        name: str,
    ) -> BookmarkFolderPayload:
        """Create a new bookmark folder."""
        from core.engagement.bookmark_services import BookmarkService
        from core.shared.exceptions import BookmarkError

        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return BookmarkFolderPayload(
                success=False,
                message="Authentication required",
                error_code="UNAUTHORIZED",
            )

        try:
            result = BookmarkService.create_folder(user_id, name)
            return BookmarkFolderPayload(
                success=True,
                folder=BookmarkFolderType(
                    id=result.id,
                    name=result.name,
                    saved_count=result.saved_count,
                    created_at=result.created_at,
                    thumbnail_url=result.thumbnail_url,
                ),
            )
        except BookmarkError as e:
            return BookmarkFolderPayload(success=False, message=e.message, error_code=e.code)

    @strawberry.mutation(description="Delete a bookmark folder")
    def delete_bookmark_folder(
        self, info: strawberry.types.Info, folder_id: str
    ) -> DeleteFolderPayload:
        """Delete a bookmark folder. Posts are moved to 'All'."""
        from core.engagement.bookmark_services import BookmarkService
        from core.shared.exceptions import BookmarkError

        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return DeleteFolderPayload(
                success=False,
                message="Authentication required",
                error_code="UNAUTHORIZED",
            )

        try:
            result = BookmarkService.delete_folder(user_id, folder_id)
            count = result["moved_posts_count"]
            return DeleteFolderPayload(
                success=True,
                moved_posts_count=count,
                message=f"Folder deleted. {count} post(s) moved to 'All'.",
            )
        except BookmarkError as e:
            return DeleteFolderPayload(success=False, message=e.message, error_code=e.code)

    @strawberry.mutation(description="Remove multiple bookmarks at once")
    def bulk_remove_bookmarks(
        self,
        info: strawberry.types.Info,
        post_ids: list[str],
    ) -> BulkRemovePayload:
        """Bulk remove bookmarks across folders."""
        from core.engagement.bookmark_services import BookmarkService

        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return BulkRemovePayload(
                success=False,
                message="Authentication required",
                error_code="UNAUTHORIZED",
            )

        result = BookmarkService.bulk_remove_bookmarks(user_id, post_ids)
        return BulkRemovePayload(
            success=True,
            removed_count=result["removed_count"],
            message=f"{result['removed_count']} bookmark(s) removed.",
        )

    @strawberry.mutation(description="Share a post to another user")
    def share_post_direct(
        self,
        info: strawberry.types.Info,
        post_id: str,
        recipient_id: str,
    ) -> SharePayload:
        """Share a post directly to another user."""
        from core.engagement.share_services import ShareService
        from core.shared.exceptions import ShareError

        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return SharePayload(
                success=False,
                message="Authentication required",
                error_code="UNAUTHORIZED",
            )

        try:
            result = ShareService.share_post_direct(user_id, post_id, recipient_id)
            from core.posts.services import PostService

            p = PostService.get_post(post_id, user_id)
            stats = PostStats(
                likes_count=p.stats.likes_count,
                comments_count=p.stats.comments_count,
                shares_count=p.stats.shares_count,
                saves_count=p.stats.saves_count,
            )
            return SharePayload(success=True, share_id=result.share_id, stats=stats)
        except ShareError as e:
            return SharePayload(
                success=False,
                message=e.message,
                error_code=e.code,
                error=ErrorType(code=e.code, message=e.message),
            )

    @strawberry.mutation(description="Share a post externally (generate link)")
    def share_post_external(self, info: strawberry.types.Info, post_id: str) -> SharePayload:
        """Share a post externally."""
        from core.engagement.share_services import ShareService
        from core.shared.exceptions import ShareError

        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return SharePayload(
                success=False,
                message="Authentication required",
                error_code="UNAUTHORIZED",
            )

        try:
            result = ShareService.share_post_external(user_id, post_id)
            from core.posts.services import PostService

            p = PostService.get_post(post_id, user_id)
            stats = PostStats(
                likes_count=p.stats.likes_count,
                comments_count=p.stats.comments_count,
                shares_count=p.stats.shares_count,
                saves_count=p.stats.saves_count,
            )
            return SharePayload(
                success=True,
                share_id=result.share_id,
                share_url=result.share_url,
                stats=stats,
            )
        except ShareError as e:
            return SharePayload(
                success=False,
                message=e.message,
                error_code=e.code,
                error=ErrorType(code=e.code, message=e.message),
            )

    @strawberry.mutation(description="Hide a post from the current user's feed")
    def hide_post(self, info: strawberry.types.Info, post_id: str) -> HidePostPayload:
        """Hide a post from the feed."""
        from core.engagement.services import EngagementService
        from core.shared.exceptions import EngagementError

        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return HidePostPayload(
                success=False,
                message="Authentication required",
                error_code="UNAUTHORIZED",
            )

        try:
            success = EngagementService.hide_post(user_id, post_id)
            return HidePostPayload(success=success)
        except EngagementError as e:
            return HidePostPayload(
                success=False,
                message=e.message,
                error_code=e.code,
                error=ErrorType(code=e.code, message=e.message),
            )

    @strawberry.mutation(description="Unhide a previously hidden post")
    def unhide_post(self, info: strawberry.types.Info, post_id: str) -> HidePostPayload:
        """Unhide a post."""
        from core.engagement.services import EngagementService

        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return HidePostPayload(
                success=False, message="Authentication required", error_code="UNAUTHORIZED"
            )

        success = EngagementService.unhide_post(user_id, post_id)
        return HidePostPayload(success=success)


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

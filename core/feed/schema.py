"""GraphQL types and queries for the feed domain."""


import strawberry

from core.users.schema import _get_authenticated_user_id


@strawberry.type
class FeedPostAuthor:
    """Author info within a feed post."""

    id: str
    username: str
    avatar_url: str | None = None


@strawberry.type
class FeedPostStats:
    """Engagement stats for a feed post."""

    likes_count: int = 0
    comments_count: int = 0
    shares_count: int = 0
    saves_count: int = 0


@strawberry.type
class FeedViewerState:
    """Viewer's relationship to a feed post."""

    liked: bool = False
    saved: bool = False
    following_author: bool = False
    is_owner: bool = False


@strawberry.type
class FeedPost:
    """A post in the feed."""

    id: str
    type: str
    caption: str | None = None
    category_id: str | None = None
    author: FeedPostAuthor
    stats: FeedPostStats
    viewer_state: FeedViewerState | None = None
    share_url: str
    created_at: str


@strawberry.type
class UserSuggestion:
    """Suggested user for empty feed states."""

    id: str
    username: str
    avatar_url: str | None = None
    bio: str | None = None
    followers_count: int = 0


@strawberry.type
class EmptyState:
    """Empty state info for empty feeds."""

    message: str
    suggestions: list[UserSuggestion]


@strawberry.type
class FeedResponse:
    """Feed query response."""

    posts: list[FeedPost]
    next_cursor: str | None = None
    has_more: bool = False
    empty_state: EmptyState | None = None


def _dto_to_feed_post(dto) -> FeedPost:
    """Convert a PostResponseDTO to a FeedPost GraphQL type."""
    return FeedPost(
        id=dto.id,
        type=dto.type,
        caption=dto.caption,
        category_id=dto.category_id,
        author=FeedPostAuthor(
            id=dto.author.id,
            username=dto.author.username,
            avatar_url=dto.author.avatar_url,
        ),
        stats=FeedPostStats(
            likes_count=dto.stats.likes_count,
            comments_count=dto.stats.comments_count,
            shares_count=dto.stats.shares_count,
            saves_count=dto.stats.saves_count,
        ),
        viewer_state=(
            FeedViewerState(
                liked=dto.viewer_state.liked,
                saved=dto.viewer_state.saved,
                following_author=dto.viewer_state.following_author,
                is_owner=dto.viewer_state.is_owner,
            )
            if dto.viewer_state
            else None
        ),
        share_url=dto.share_url,
        created_at=dto.created_at,
    )


@strawberry.type
class FeedQueries:
    """Feed domain GraphQL queries."""

    @strawberry.field(description="Get the For You feed")
    def for_you_feed(
        self,
        info: strawberry.types.Info,
        cursor: str | None = None,
        limit: int = 20,
    ) -> FeedResponse:
        """Get personalized For You feed."""
        from core.feed.services import FeedService

        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return FeedResponse(posts=[], has_more=False)

        result = FeedService.get_for_you_feed(user_id=user_id, cursor=cursor, limit=limit)

        return FeedResponse(
            posts=[_dto_to_feed_post(p) for p in result.posts],
            next_cursor=result.next_cursor,
            has_more=result.has_more,
            empty_state=(
                EmptyState(
                    message=result.empty_state.message,
                    suggestions=[
                        UserSuggestion(
                            id=s.id,
                            username=s.username,
                            avatar_url=s.avatar_url,
                            bio=s.bio,
                            followers_count=s.followers_count,
                        )
                        for s in result.empty_state.suggestions
                    ],
                )
                if result.empty_state
                else None
            ),
        )

    @strawberry.field(description="Get the Following feed")
    def following_feed(
        self,
        info: strawberry.types.Info,
        cursor: str | None = None,
        limit: int = 20,
    ) -> FeedResponse:
        """Get chronological feed from followed users."""
        from core.feed.services import FeedService

        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return FeedResponse(posts=[], has_more=False)

        result = FeedService.get_following_feed(user_id=user_id, cursor=cursor, limit=limit)

        return FeedResponse(
            posts=[_dto_to_feed_post(p) for p in result.posts],
            next_cursor=result.next_cursor,
            has_more=result.has_more,
            empty_state=(
                EmptyState(
                    message=result.empty_state.message,
                    suggestions=[
                        UserSuggestion(
                            id=s.id,
                            username=s.username,
                            avatar_url=s.avatar_url,
                            bio=s.bio,
                            followers_count=s.followers_count,
                        )
                        for s in result.empty_state.suggestions
                    ],
                )
                if result.empty_state
                else None
            ),
        )

    @strawberry.field(description="Get the Discover feed by category")
    def discover_feed(
        self,
        info: strawberry.types.Info,
        category: str | None = None,
        media_type: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> FeedResponse:
        """Get category-based discovery feed."""
        from core.feed.services import FeedService

        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return FeedResponse(posts=[], has_more=False)

        result = FeedService.get_discover_feed(
            user_id=user_id, category=category, media_type=media_type, cursor=cursor, limit=limit
        )

        return FeedResponse(
            posts=[_dto_to_feed_post(p) for p in result.posts],
            next_cursor=result.next_cursor,
            has_more=result.has_more,
        )

"""GraphQL types and queries for the feed domain."""


import strawberry

from core.media.schema import MediaFileType
from core.shared.types import MediaType as MediaTypeEnum
from core.users.schema import _get_authenticated_user_id


@strawberry.type
class CategoryType:
    """
    Discovery category strictly formatted for mobile frontend rendering.

    **Icon/Color handling:**
    Backend provides string identifiers (icon: "heart", bgColor: "#FADADD").
    Frontend maps icon strings to icon components natively.
    """

    id: str
    label: str
    slug: str
    icon: str
    bg_color: str
    bd_color: str
    order: int | None = None


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
    """
    A post in the feed efficiently packed with discovery metadata.

    Includes mobile Field aliases enabling direct ingestion without serialization logic.
    """

    id: str
    type: str
    caption: str | None = None
    category_id: str | None = None
    author: FeedPostAuthor
    stats: FeedPostStats
    viewer_state: FeedViewerState | None = None
    share_url: str
    created_at: str

    _media_list: list[MediaFileType] = strawberry.field(
        default_factory=list, description="Private holder for extracted media."
    )

    @strawberry.field(description="Post text content securely (alias for caption)")
    def text(self) -> str | None:
        """Post caption/text - mobile expects 'text' field cleanly"""
        return self.caption

    @strawberry.field(description="Media files array natively (alias for mapped media DTO)")
    def media(self) -> list[MediaFileType]:
        """Media files array mapped cleanly matching exactly what mobile expects"""
        return self._media_list


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
    """
    Paginated feed array with injected algorithmic empty states.

    **Authentication:** Optional depending on the query
    **Related operations:** for_you_feed, following_feed
    """

    posts: list[FeedPost]
    next_cursor: str | None = None
    has_more: bool = False
    empty_state: EmptyState | None = None


def _dto_to_feed_post(dto) -> FeedPost:
    """Convert a PostResponseDTO seamlessly to a FeedPost GraphQL type validating arrays."""

    media_list = []
    if dto.media:
        if dto.type == "video":
            media_list.append(
                MediaFileType(
                    id=dto.id,
                    url=dto.media.url,
                    thumbnail=getattr(dto.media, "thumbnail_url", None) or dto.media.url,
                    type=MediaTypeEnum.VIDEO,
                    width=getattr(dto.media, "width", None),
                    height=getattr(dto.media, "height", None),
                )
            )
        elif dto.type == "image":
            for img in dto.media.items:
                media_list.append(
                    MediaFileType(
                        id=getattr(img, "id", dto.id),
                        url=img.url,
                        thumbnail=getattr(img, "thumbnail_url", None) or img.url,
                        type=MediaTypeEnum.IMAGE,
                        width=getattr(img, "width", None),
                        height=getattr(img, "height", None),
                    )
                )

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
        _media_list=media_list,
    )


@strawberry.type
class FeedQueries:
    """Feed domain GraphQL queries globally handling Discovery."""

    @strawberry.field(
        description="Get all discovery categories securely formatted for algorithmic content filtering."
    )
    def discover_categories(self) -> list[CategoryType]:
        """
        Get discovery categories packaged with specific icons and Figma hex colors natively.

        Returns exactly 6 categories: All (default), Love, Trust, Worship, Patience, Prayer.

        **Authentication:** Not required
        **Parameters:** None
        **Returns:** Array of valid DiscoverCategory objects
        **Errors:** None
        """
        return [
            CategoryType(
                id="all",
                label="All",
                slug="all",
                icon="circle-stack",
                bg_color="#F5EDD7",
                bd_color="#E5D5B0",
                order=0,
            ),
            CategoryType(
                id="love",
                label="Love",
                slug="love",
                icon="heart",
                bg_color="#FADADD",
                bd_color="#F5B8C0",
                order=1,
            ),
            CategoryType(
                id="trust",
                label="Trust",
                slug="trust",
                icon="shield",
                bg_color="#D4E3F7",
                bd_color="#B8CEE8",
                order=2,
            ),
            CategoryType(
                id="worship",
                label="Worship",
                slug="worship",
                icon="harp",
                bg_color="#FFF4D4",
                bd_color="#F5E8B0",
                order=3,
            ),
            CategoryType(
                id="patience",
                label="Patience",
                slug="patience",
                icon="hourglass",
                bg_color="#F0E6F7",
                bd_color="#DCC8E8",
                order=4,
            ),
            CategoryType(
                id="prayer",
                label="Prayer",
                slug="prayer",
                icon="praying-hands",
                bg_color="#D4F7F0",
                bd_color="#B8E8DD",
                order=5,
            ),
        ]

    @strawberry.field(
        description="Get the public or personalized feed. Works with or without authentication."
    )
    def feed(
        self,
        info: strawberry.types.Info,
        limit: int = 20,
        cursor: str | None = None,
    ) -> FeedResponse:
        """
        Get public feed.

        **Authentication:** OPTIONAL
        - Authenticated: Personalized feed based on follows
        - Unauthenticated: Public discovery feed
        """
        from core.feed.services import FeedService

        user_id = _get_authenticated_user_id(info)

        result = FeedService.get_feed(
            viewer_id=user_id,
            cursor=cursor,
            limit=limit,
        )

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

    @strawberry.field(description="Get the personalized For You content feed reliably.")
    def for_you_feed(
        self,
        info: strawberry.types.Info,
        cursor: str | None = None,
        limit: int = 20,
    ) -> FeedResponse:
        """
        Get the personalized Discovery layout For You feed globally.

        Blends recent followed content with algorithmically fetched public posts bounded
        by specific Interests and Engagement data. Implements cursor pagination seamlessly.

        **Authentication:** Optional (will yield default public feed if UNAUTHENTICATED)
        **Parameters:**
        - cursor (String, optional) - Pass specific opaque token forward
        - limit (Int, optional) - Volume controls
        **Returns:** FeedResponse mapped strictly containing exactly requested posts
        **Errors:** Return empty states accurately natively on issues safely.
        """
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

    @strawberry.field(description="Retrieve the strict chronological Following feed.")
    def following_feed(
        self,
        info: strawberry.types.Info,
        cursor: str | None = None,
        limit: int = 20,
    ) -> FeedResponse:
        """
        Get strictly chronological feed consisting only of authors the user follows.

        Yields `has_more` Boolean flag to track scrolling natively. Fails explicitly into
        an `empty_state` object containing 4 User Suggestions to bootstrap the feed seamlessly
        if no Following data exists natively.

        **Authentication:** Required
        **Parameters:**
        - cursor (String, optional) - Pass specific token directly
        - limit (Int, optional) - Size bounds
        **Returns:** FeedResponse containing post entities directly natively
        **Errors:** Return empty states with UserSuggestion array if no data exists natively.
        """
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

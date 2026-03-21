"""GraphQL types and queries for the feed domain."""


import dataclasses
import logging

import strawberry

from core.media.schema import MediaFileType
from core.shared.types import MediaType as MediaTypeEnum
from core.shared.types import PostType, ScriptureVerse
from core.users.schema import _get_authenticated_user_id

logger = logging.getLogger("core.feed")


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
class FeedPostScripture:
    """Scripture reference within a feed post."""

    reference: str
    text: str
    translation: str = strawberry.field(name="translation", default="KJV")
    book: str
    chapter: int
    verse_start: int
    verse_end: int | None = None
    verses: list[ScriptureVerse] = strawberry.field(default_factory=list)


@strawberry.type
class ImageData:
    items: list[MediaFileType]


@strawberry.type
class VideoData:
    url: str
    thumbnail_url: str | None = None


@strawberry.type
class TextData:
    message: str | None = None
    scripture: FeedPostScripture | None = None


@strawberry.type
class FeedPost:
    """
    A post in the feed efficiently packed with discovery metadata.

    Includes mobile Field aliases enabling direct ingestion without serialization logic.
    """

    id: str
    post_type: PostType = strawberry.field(name="type")
    caption: str | None = None
    _category_id: strawberry.Private[str | None] = None
    author: FeedPostAuthor
    stats: FeedPostStats
    viewer_state: FeedViewerState | None = None
    share_url: str
    created_at: str
    _scripture: strawberry.Private[FeedPostScripture | None] = None

    _media_list: strawberry.Private[list[MediaFileType]] = dataclasses.field(default_factory=list)

    @strawberry.field(description="Image data array mapping")
    def image(self) -> ImageData | None:
        if self.post_type == PostType.MEDIA and any(
            m.type == MediaTypeEnum.IMAGE for m in self._media_list
        ):
            return ImageData(items=[m for m in self._media_list if m.type == MediaTypeEnum.IMAGE])
        return None

    @strawberry.field(description="Video metadata mapping")
    def video(self) -> VideoData | None:
        if self.post_type == PostType.MEDIA and any(
            m.type == MediaTypeEnum.VIDEO for m in self._media_list
        ):
            v = next(m for m in self._media_list if m.type == MediaTypeEnum.VIDEO)
            return VideoData(url=v.url, thumbnail_url=v.thumbnail)
        return None

    @strawberry.field(description="Text/Bible content cleanly segregated")
    def text(self) -> TextData | None:
        if self.post_type in (PostType.TEXT, PostType.BIBLE):
            return TextData(message=self.caption, scripture=self._scripture)
        return None

    @strawberry.field(description="Full category object natively")
    def category(self) -> CategoryType | None:
        if not self._category_id:
            return None
        # discover_categories is defined lower, just call it directly
        queries = FeedQueries()
        categories = queries.discover_categories()
        for cat in categories:
            if cat.id == self._category_id:
                return CategoryType(
                    id=cat.id,
                    label=cat.label,
                    slug=cat.slug,
                    icon=cat.icon,
                    bg_color=cat.bg_color,
                    bd_color=cat.bd_color,
                    order=cat.order,
                )
        logger.warning("Category not found category_id=%s", self._category_id)
        return None


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
                    thumbnail_url=getattr(dto.media, "thumbnail_url", None) or dto.media.url,
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
                        thumbnail_url=getattr(img, "thumbnail_url", None) or img.url,
                        type=MediaTypeEnum.IMAGE,
                        width=getattr(img, "width", None),
                        height=getattr(img, "height", None),
                    )
                )

    post_t = dto.type.lower() if getattr(dto, "type", None) else "text"
    mapped_type = (
        PostType.MEDIA
        if post_t in ("image", "video")
        else (PostType.BIBLE if post_t == "bible" else PostType.TEXT)
    )

    if post_t not in ("image", "video", "text", "bible"):
        logger.warning("Unknown post type in feed DTO dto_type=%s post_id=%s", post_t, dto.id)

    return FeedPost(
        id=dto.id,
        post_type=mapped_type,
        caption=dto.caption,
        _category_id=dto.category_id,
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
        _scripture=(
            FeedPostScripture(
                reference=dto.scripture.reference,
                text=dto.scripture.text,
                version=dto.scripture.version,
                book=dto.scripture.book,
                chapter=dto.scripture.chapter,
                verse_start=dto.scripture.verse_start,
                verse_end=dto.scripture.verse_end,
                verses=[
                    ScriptureVerse(number=v.number, text=v.text)
                    for v in getattr(dto.scripture, "verses", [])
                ],
            )
            if dto.scripture
            else None
        ),
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
        from core.categories.models import Category

        categories = Category.objects.all().order_by("order")
        return [
            CategoryType(
                id=cat.id,
                label=cat.label,
                slug=cat.slug,
                icon=cat.icon,
                bg_color=cat.bg_color,
                bd_color=cat.bd_color,
                order=cat.order,
            )
            for cat in categories
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
        logger.info("feed_query user_id=%s limit=%d cursor=%s", user_id, limit, cursor)

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
            user_id=user_id, category=category, cursor=cursor, limit=limit
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

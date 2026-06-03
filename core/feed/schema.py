"""GraphQL types and queries for the feed domain."""


import dataclasses
import logging
import typing

import strawberry

from core.media.schema import MediaFileType
from core.scripture.constants import normalize_translation
from core.shared.types import MediaType as MediaTypeEnum
from core.shared.types import PostType, ScriptureVerse
from core.users.schema import _get_authenticated_user_id

logger = logging.getLogger("core.feed")


def _get_category_by_id(category_id: str) -> "CategoryType | None":
    """Fetch a single category from the shared cache (max 1 DB call per process per 5 min)."""
    from django.core.cache import cache

    from core.categories.models import Category

    cache_key = "all_categories_v1"
    categories_map: dict | None = cache.get(cache_key)

    if categories_map is None:
        qs = Category.objects.all().order_by("order")
        categories_map = {cat.id: cat for cat in qs}
        cache.set(cache_key, categories_map, 300)

    cat = categories_map.get(category_id)
    if not cat:
        return None
    return CategoryType(
        id=cat.id,
        label=cat.label,
        slug=cat.slug,
        icon=cat.icon,
        bg_color=cat.bg_color,
        bd_color=cat.bd_color,
        text_post_bg=cat.text_post_bg,
        order=cat.order,
    )


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
    text_post_bg: str | None = None
    order: int | None = None


@strawberry.type
class FeedPostAuthor:
    """Author info within a feed post."""

    id: str
    username: str
    avatar_url: str | None = None


@strawberry.type
class FeedPostStats:
    """Engagement stats with formatted display counts."""

    _likes: strawberry.Private[int] = 0
    _comments: strawberry.Private[int] = 0
    _shares: strawberry.Private[int] = 0
    _saves: strawberry.Private[int] = 0

    @strawberry.field
    def likes_count(self) -> str:
        """Formatted likes count (e.g., "1.2k")."""
        from core.shared.utils import format_count

        return format_count(self._likes)

    @strawberry.field
    def comments_count(self) -> str:
        from core.shared.utils import format_count

        return format_count(self._comments)

    @strawberry.field
    def shares_count(self) -> str:
        from core.shared.utils import format_count

        return format_count(self._shares)

    @strawberry.field
    def saves_count(self) -> str:
        from core.shared.utils import format_count

        return format_count(self._saves)


@strawberry.type
class FeedViewerState:
    """Viewer's relationship to a feed post."""

    liked: bool = False
    saved: bool = False
    following_author: bool = False
    followed_by_author: bool = False
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
    duration: int | None = None
    width: int | None = None
    height: int | None = None


@strawberry.type
class FeedPost:
    """
    A post in the feed efficiently packed with discovery metadata.

    Content field contract:
    - ``textMessage`` — populated for TEXT and BIBLE posts only.
    - ``caption``     — populated for MEDIA posts only.
    - ``text``        — raw backward-compat alias; always returns the stored value.
    """

    id: str
    post_type: PostType = strawberry.field(name="type")
    # Internal backing field — holds the DB caption value for all post types.
    # Not exposed directly; use the computed fields below instead.
    _caption: strawberry.Private[str | None] = None
    _category_id: strawberry.Private[str | None] = None
    author: FeedPostAuthor
    stats: FeedPostStats
    viewer_state: FeedViewerState | None = None
    share_url: str
    created_at: str
    scripture: FeedPostScripture | None = None
    saved_in_folders: list[
        typing.Annotated["BookmarkFolderType", strawberry.lazy("core.engagement.schema")]  # noqa: F821
    ] | None = None

    _media_list: strawberry.Private[list[MediaFileType]] = dataclasses.field(default_factory=list)

    def _primary_media(self) -> MediaFileType | None:
        videos = [m for m in self._media_list if m.type == MediaTypeEnum.VIDEO]
        if videos:
            return videos[0]
        images = [m for m in self._media_list if m.type == MediaTypeEnum.IMAGE]
        if images:
            return images[0]
        return None

    @strawberry.field(description="Caption for MEDIA posts. Null for TEXT and BIBLE posts.")
    def caption(self) -> str | None:
        """Returns the caption only when this is a MEDIA post."""
        if self.post_type == PostType.MEDIA:
            return self._caption
        return None

    @strawberry.field(
        name="textMessage",
        description="Content body for TEXT posts only. Null for MEDIA and BIBLE posts.",
    )
    def text_message(self) -> str | None:
        """Returns the text content only for TEXT posts."""
        if self.post_type == PostType.TEXT:
            return self._caption
        return None

    @strawberry.field(
        name="bibleMessage",
        description="Caption/note attached to a BIBLE post. Null for TEXT and MEDIA posts.",
    )
    def bible_message(self) -> str | None:
        """Returns the caption content only for BIBLE posts."""
        if self.post_type == PostType.BIBLE:
            return self._caption
        return None

    @strawberry.field(name="mediaUrl", description="Primary flat media URL for image/video posts.")
    def media_url(self) -> str | None:
        primary_media = self._primary_media()
        return primary_media.url if primary_media else None

    @strawberry.field(
        name="mediaType", description="Primary flat media type: image, video, or null."
    )
    def media_type(self) -> str | None:
        primary_media = self._primary_media()
        if not primary_media:
            return None
        if primary_media.type == MediaTypeEnum.VIDEO:
            return "video"
        if primary_media.type == MediaTypeEnum.IMAGE:
            return "image"
        return None

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
            return VideoData(
                url=v.url,
                thumbnail_url=v.thumbnail_url,
                duration=v.duration,
                width=v.width,
                height=v.height,
            )
        return None

    @strawberry.field(description="Full category object natively")
    def category(self) -> CategoryType | None:
        if not self._category_id:
            return None
        return _get_category_by_id(self._category_id)


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

    from core.shared.dtos import ImageMediaDTO, VideoMediaDTO

    media_list = []
    if dto.media and dto.type in ("image", "video"):
        if dto.type == "video" and isinstance(dto.media, VideoMediaDTO):
            media_list.append(
                MediaFileType(
                    id=dto.id,
                    url=dto.media.url,
                    thumbnail_url=getattr(dto.media, "thumbnail_url", None) or dto.media.url,
                    type=MediaTypeEnum.VIDEO,
                    width=getattr(dto.media, "width", None),
                    height=getattr(dto.media, "height", None),
                    duration=getattr(dto.media, "duration", None),
                )
            )
        elif dto.type == "image" and isinstance(dto.media, ImageMediaDTO):
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
        _caption=dto.caption,
        _category_id=dto.category_id,
        author=FeedPostAuthor(
            id=dto.author.id,
            username=dto.author.username,
            avatar_url=dto.author.avatar_url,
        ),
        stats=FeedPostStats(
            _likes=dto.stats.likes_count,
            _comments=dto.stats.comments_count,
            _shares=dto.stats.shares_count,
            _saves=dto.stats.saves_count,
        ),
        viewer_state=(
            FeedViewerState(
                liked=dto.viewer_state.liked,
                saved=dto.viewer_state.saved,
                following_author=dto.viewer_state.following_author,
                followed_by_author=getattr(dto.viewer_state, "followed_by_author", False),
                is_owner=dto.viewer_state.is_owner,
            )
            if dto.viewer_state
            else None
        ),
        share_url=dto.share_url,
        created_at=dto.created_at,
        scripture=(
            FeedPostScripture(
                reference=dto.scripture.reference,
                text=dto.scripture.text,
                translation=normalize_translation(dto.scripture.version),
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
        saved_in_folders=[
            typing.cast(
                typing.Any,
                {
                    "id": folder.get("id"),
                    "name": folder.get("name"),
                    "saved_count": folder.get("saved_count", 0),
                    "created_at": folder.get("created_at", ""),
                },
            )
            for folder in getattr(dto, "saved_in_folders", []) or []
        ],
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
                text_post_bg=cat.text_post_bg,
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

        # Architecturally sound fallback: Unauthenticated users get the public discovery feed
        # instead of a broken empty array.
        if not user_id:
            result = FeedService.get_feed(viewer_id=None, cursor=cursor, limit=limit)
        else:
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

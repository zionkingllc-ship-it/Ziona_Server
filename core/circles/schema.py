# ──────────────────────────────────────────────
#  Enums
# ──────────────────────────────────────────────
import dataclasses
import enum
from datetime import datetime

import strawberry
from strawberry.types import Info

from core.circles.services import (
    create_circle_post,
    ensure_circle_post_liked,
    get_all_circles,
    get_circle_by_id,
    get_circle_feed,
    get_circle_post,
    get_my_circles,
    get_suggested_circles,
    join_circle,
    leave_circle,
    like_anchor,
    like_circle_post,
    pray_for_anchor,
    pray_for_circle_post,
)
from core.feed.schema import ImageData, VideoData
from core.media.schema import MediaFileType
from core.shared.exceptions import ZionaError
from core.shared.types import ErrorType, PageInfo
from core.shared.types import MediaType as GraphQLMediaType
from core.users.schema import UserType, _get_authenticated_user_id


@strawberry.enum
class AnchorTypeEnum(enum.Enum):
    BIBLE_VERSE = "bible_verse"
    DEVOTIONAL = "devotional"
    TEXT = "text"
    IMAGE = "image"
    VIDEO = "video"
    IMAGE_TEXT = "image_text"


@strawberry.enum
class CirclePostFilterEnum(enum.Enum):
    NEW = "NEW"
    TRENDING = "TRENDING"
    VIEWER_POSTS = "VIEWER_POSTS"


# ──────────────────────────────────────────────
#  Strawberry Types
# ──────────────────────────────────────────────


@strawberry.type
class ScriptureReference:
    book: str
    chapter: int | None = None
    verse_start: int | None = strawberry.field(name="verseStart", default=None)
    verse_end: int | None = strawberry.field(name="verseEnd", default=None)
    translation: str = strawberry.field(name="translation", default="KJV")
    text: str = ""


@strawberry.type
class AnchorPageType:
    page_number: int = strawberry.field(name="pageNumber")
    title: str
    content: str
    media_url: str = strawberry.field(name="mediaUrl", default="")


def _anchor_date_value(anchor) -> str:
    """Return the ISO calendar date used for mobile anchor filtering."""
    return anchor.published_at.date().isoformat()


def _unique_anchor_dates(*anchor_groups) -> list[str]:
    """Return unique anchor dates while preserving newest-first query order."""
    seen: set[str] = set()
    dates: list[str] = []
    for group in anchor_groups:
        if not group:
            continue
        anchors = group if isinstance(group, list | tuple) else [group]
        for anchor in anchors:
            if not anchor:
                continue
            anchor_date = _anchor_date_value(anchor)
            if anchor_date not in seen:
                seen.add(anchor_date)
                dates.append(anchor_date)
    return dates


# ──────────────────────────────────────────────
#  Viewer State Types
# ──────────────────────────────────────────────


@strawberry.type
class CirclePostViewerState:
    """Viewer's engagement state on a CirclePost."""

    liked: bool = False
    prayed: bool = False


@strawberry.type
class AnchorViewerState:
    """Viewer's engagement state on an Anchor."""

    liked: bool = False
    prayed: bool = False


@strawberry.type
class AnchorType:
    id: str
    title: str
    content: str

    @strawberry.field(name="anchorType")
    def anchor_type(self) -> str:
        return self._dto.anchor_type

    @strawberry.field(name="type")
    def mobile_type(self) -> str:
        return self._dto.anchor_type

    @strawberry.field(name="createdAt")
    def created_at(self) -> datetime:
        return self._dto.created_at

    @strawberry.field(name="publishedAt")
    def published_at(self) -> datetime:
        return self._dto.published_at

    @strawberry.field
    def date(self) -> str:
        return _anchor_date_value(self._dto)

    @strawberry.field(name="anchorDate")
    def anchor_date(self) -> str:
        return _anchor_date_value(self._dto)

    @strawberry.field(name="expiresAt")
    def expires_at(self) -> datetime:
        return self._dto.expires_at

    @strawberry.field(name="timeRemaining")
    def time_remaining(self) -> str:
        return self._dto.get_time_remaining()

    @strawberry.field(name="isActive")
    def is_active(self) -> bool:
        return self._dto.is_active

    @strawberry.field(name="isExpired")
    def is_expired(self) -> bool:
        """True once the anchor's 24-hour window has passed.

        Computed in Python from the already-loaded expires_at field — zero
        extra DB queries. The mobile app uses this flag to decide whether to
        render the anchor as active or show it in the expired history list.
        """
        from django.utils import timezone

        return timezone.now() >= self._dto.expires_at

    @strawberry.field(name="responseCount")
    def response_count(self) -> int:
        if hasattr(self._dto, "response_count"):
            return self._dto.response_count
        return self._dto.get_response_count()

    @strawberry.field(name="mediaUrl")
    def media_url(self) -> str | None:
        return self._dto.media_url or None

    @strawberry.field
    def scripture(self) -> str | None:
        return self.bible_reference()

    @strawberry.field(name="likedImage")
    def liked_image(self) -> int | None:
        # Kept for backward compatibility.
        # Mobile dev: migrate to viewerState.liked for per-user like state.
        return 1 if self._dto.anchor_liked_count else None

    @strawberry.field(name="viewerState")
    def viewer_state(self, info: Info) -> AnchorViewerState | None:
        """Per-user like/pray state for this anchor.

        Dynamically resolved (not cached) because the Anchor object itself
        is served from Redis — the viewer state must always be fresh.
        """
        from core.circles.models import AnchorEngagement

        viewer_id = _get_authenticated_user_id(info)
        if not viewer_id:
            return AnchorViewerState(liked=False, prayed=False)

        engagements = set(
            AnchorEngagement.objects.filter(
                anchor_id=self.id,
                user_id=viewer_id,
            ).values_list("engagement_type", flat=True)
        )
        return AnchorViewerState(
            liked="like" in engagements,
            prayed="pray" in engagements,
        )

    @strawberry.field(name="bibleReference")
    def bible_reference(self) -> str | None:
        if not self._dto.scripture_book:
            return None

        reference = self._dto.scripture_book
        if self._dto.scripture_chapter:
            reference = f"{reference} {self._dto.scripture_chapter}"
        if self._dto.scripture_verse_start:
            reference = f"{reference}:{self._dto.scripture_verse_start}"
            if (
                self._dto.scripture_verse_end
                and self._dto.scripture_verse_end != self._dto.scripture_verse_start
            ):
                reference = f"{reference}-{self._dto.scripture_verse_end}"
        return reference

    @strawberry.field(name="bibleText")
    def bible_text(self) -> str | None:
        return self._dto.scripture_text or None

    @strawberry.field(name="scriptureReference")
    def scripture_reference(self) -> ScriptureReference | None:
        if self._dto.anchor_type != "bible_verse" or not self._dto.scripture_book:
            return None
        return ScriptureReference(
            book=self._dto.scripture_book,
            chapter=self._dto.scripture_chapter,
            verse_start=self._dto.scripture_verse_start,
            verse_end=self._dto.scripture_verse_end,
            translation=self._dto.scripture_translation,
            text=self._dto.scripture_text,
        )

    @strawberry.field
    def pages(self) -> list[AnchorPageType]:
        if self._dto.anchor_type != "devotional":
            return []
        return [
            AnchorPageType(
                page_number=p.page_number,
                title=p.title,
                content=p.content,
                media_url=p.media_url or "",
            )
            for p in self._dto.pages.all()
        ]

    @strawberry.field
    def author(self) -> UserType | None:
        if self._dto.created_by:
            return UserType.from_db_model(self._dto.created_by)
        return None

    # ── Mobile engagement counters ──────────────────────────────────────────

    @strawberry.field(name="prayedCount")
    def prayed_count(self) -> int:
        return self._dto.prayed_count or 0

    @strawberry.field(name="anchorLikedCount")
    def anchor_liked_count(self) -> int:
        return self._dto.anchor_liked_count or 0

    # ── Visual / theming fields ──────────────────────────────────────────────

    @strawberry.field(name="backgroundColors")
    def background_colors(self) -> list[str] | None:
        val = self._dto.background_colors
        return val if val else None

    @strawberry.field(name="backgroundImage")
    def background_image(self) -> str | None:
        return self._dto.background_image or None

    @strawberry.field(name="anchorText")
    def anchor_text(self) -> str | None:
        return self._dto.anchor_text or None

    @strawberry.field(name="anchorVerse")
    def anchor_verse(self) -> str | None:
        return self._dto.anchor_verse or None

    @strawberry.field(name="anchorImageText")
    def anchor_image_text(self) -> str | None:
        return self._dto.anchor_image_text or None

    @strawberry.field(name="anchorThumbnail")
    def anchor_thumbnail(self) -> str | None:
        return self._dto.anchor_thumbnail or self._dto.preview_url or None

    @strawberry.field(name="anchorImage")
    def anchor_image(self) -> str | None:
        if self._dto.anchor_image:
            return self._dto.anchor_image
        if self._dto.anchor_type in ("image", "image_text"):
            return self._dto.media_url or None
        return None

    @strawberry.field(name="anchorVideo")
    def anchor_video(self) -> str | None:
        if self._dto.anchor_video:
            return self._dto.anchor_video
        if self._dto.anchor_type == "video":
            return self._dto.media_url or None
        return None

    @classmethod
    def from_db_model(cls, anchor_instance):
        if not anchor_instance:
            return None
        instance = cls(
            id=str(anchor_instance.id),
            title=anchor_instance.title,
            content=anchor_instance.content or "",
        )
        instance._dto = anchor_instance
        return instance


@strawberry.type
class CreateAnchorPayload:
    success: bool
    anchor: AnchorType | None = None
    error: ErrorType | None = None


@strawberry.type
class CircleRule:
    id: int
    rule_number: int = strawberry.field(name="ruleNumber")
    title: str
    description: str


# ──────────────────────────────────────────────
#  Circle Type
# ──────────────────────────────────────────────


@strawberry.type
class CircleType:
    id: str
    name: str
    description: str

    def _banner_image_url(self) -> str | None:
        return (
            self._dto.banner_image or self._dto.cover_image or self._dto.profile_image_url or None
        )

    @strawberry.field
    def title(self) -> str:
        return self._dto.name

    @strawberry.field(name="coverImage")
    def cover_image(self) -> str:
        return self._dto.cover_image

    @strawberry.field(name="suggestionCardImage")
    def suggestion_card_image(self) -> str:
        return self._dto.cover_image

    @strawberry.field
    def image(self) -> str:
        return self._dto.cover_image

    @strawberry.field(name="createdAt")
    def created_at(self) -> datetime:
        return self._dto.created_at

    @strawberry.field(name="memberCount")
    def member_count(self) -> int:
        if self._dto.display_member_count is not None:
            return self._dto.display_member_count
        if hasattr(self._dto, "member_count"):
            return self._dto.member_count
        return self._dto.get_member_count()

    @strawberry.field
    def members(self) -> int:
        return self.member_count()

    @strawberry.field(name="memberPreviews")
    def member_previews(self) -> list[UserType]:
        if hasattr(self._dto, "preview_memberships"):
            return [UserType.from_db_model(m.user) for m in self._dto.preview_memberships]
        return [UserType.from_db_model(u) for u in self._dto.get_member_previews(limit=4)]

    @strawberry.field
    def avatars(self) -> list[str]:
        if hasattr(self._dto, "preview_memberships"):
            users = [m.user for m in self._dto.preview_memberships]
        else:
            users = self._dto.get_member_previews(limit=4)
        return [user.avatar_url for user in users if getattr(user, "avatar_url", "")]

    @strawberry.field(name="memberAvatars")
    def member_avatars(self) -> list[str]:
        users = self._dto.get_member_previews(limit=8)
        return [user.avatar_url for user in users if getattr(user, "avatar_url", "")]

    @strawberry.field(name="isSubscribed")
    def is_subscribed(self, info: Info) -> bool:
        if hasattr(self._dto, "_is_viewer_subscribed"):
            return self._dto._is_viewer_subscribed

        user_id = _get_authenticated_user_id(info)
        return self._dto.is_user_subscribed(user_id)

    @strawberry.field(name="isJoined")
    def is_joined(self, info: Info) -> bool:
        return self.is_subscribed(info)

    @strawberry.field
    def rules(self) -> list[CircleRule]:
        return _circle_rules_for(self._dto)

    @strawberry.field(name="activeAnchor")
    def active_anchor(self, info: Info) -> AnchorType | None:
        from core.circles.anchor_services import get_active_anchor

        viewer_id = _get_authenticated_user_id(info)
        anchor = get_active_anchor(str(self._dto.id), viewer_id=viewer_id)
        return AnchorType.from_db_model(anchor)

    @strawberry.field(name="anchorDates")
    def anchor_dates(self, info: Info) -> list[str]:
        from core.circles.models import Anchor
        from core.engagement.cache import EngagementCache
        from core.engagement.hidden_content import exclude_hidden_circle_content

        viewer_id = _get_authenticated_user_id(info)
        if viewer_id and EngagementCache.is_circle_content_hidden(
            viewer_id, "circle", str(self._dto.id)
        ):
            return []

        anchors = Anchor.objects.filter(
            circle_id=self._dto.id,
            deleted_at__isnull=True,
        ).order_by("-published_at")
        anchors = exclude_hidden_circle_content(anchors, viewer_id, target_type="anchor")
        return _unique_anchor_dates(list(anchors))

    @strawberry.field(name="bannerImage")
    def banner_image(self) -> str | None:
        return self._banner_image_url()

    @strawberry.field(name="profileImage")
    def profile_image(self) -> str | None:
        return self._dto.profile_image_url or None

    @classmethod
    def from_db_model(cls, circle_instance):
        if not circle_instance:
            return None
        instance = cls(
            id=str(circle_instance.id),
            name=circle_instance.name,
            description=circle_instance.description,
        )
        instance._dto = circle_instance
        return instance


@strawberry.type
class JoinCirclePayload:
    success: bool
    circle: CircleType | None = None
    error: ErrorType | None = None


# ──────────────────────────────────────────────
#  Circle Feed Types
# ──────────────────────────────────────────────


@strawberry.type
class CirclePostAuthorType:
    id: str
    username: str | None = None
    name: str | None = None
    avatar_url: str | None = strawberry.field(name="avatarUrl", default=None)

    @strawberry.field
    def avatar(self) -> str | None:
        return self.avatar_url

    @classmethod
    def from_user(cls, user) -> "CirclePostAuthorType":
        instance = cls(id=str(user.id))
        instance.username = getattr(user, "username", None) or ""
        instance.name = getattr(user, "full_name", None) or getattr(user, "username", None) or ""
        instance.avatar_url = getattr(user, "avatar_url", None)
        return instance


def _media_file_to_graphql(media_file) -> MediaFileType:
    from core.shared.utils import normalize_duration_seconds

    return MediaFileType(
        id=str(media_file.id),
        url=media_file.url,
        type=GraphQLMediaType[media_file.media_type.upper()],
        width=media_file.width,
        height=media_file.height,
        thumbnail_url=media_file.thumbnail_url,
        duration=normalize_duration_seconds(media_file.duration),
    )


@strawberry.type
class CirclePostType:
    id: str
    user: CirclePostAuthorType
    created_at: datetime = strawberry.field(name="createdAt")
    text: str | None = None
    likes_count: int = strawberry.field(name="likesCount", default=0)
    comments_count: int = strawberry.field(name="commentsCount", default=0)
    prayed_count: int = strawberry.field(name="prayedCount", default=0)
    anchor_liked_count: int = strawberry.field(name="anchorLikedCount", default=0)
    _media_list: strawberry.Private[list[MediaFileType]] = dataclasses.field(default_factory=list)

    def _primary_media(self) -> MediaFileType | None:
        videos = [media for media in self._media_list if media.type == GraphQLMediaType.VIDEO]
        if videos:
            return videos[0]
        images = [media for media in self._media_list if media.type == GraphQLMediaType.IMAGE]
        if images:
            return images[0]
        return None

    @strawberry.field
    def media(self) -> list[MediaFileType]:
        return self._media_list

    @strawberry.field(name="mediaUrl")
    def media_url(self) -> str | None:
        primary_media = self._primary_media()
        return primary_media.url if primary_media else None

    @strawberry.field(name="mediaType")
    def media_type(self) -> str | None:
        primary_media = self._primary_media()
        if not primary_media:
            return None
        if primary_media.type == GraphQLMediaType.VIDEO:
            return "video"
        if primary_media.type == GraphQLMediaType.IMAGE:
            return "image"
        return None

    @strawberry.field(description="Image data array mapping")
    def image(self) -> ImageData | None:
        image_items = [media for media in self._media_list if media.type == GraphQLMediaType.IMAGE]
        if image_items:
            return ImageData(items=image_items)
        return None

    @strawberry.field(description="Video metadata mapping")
    def video(self) -> VideoData | None:
        video_item = next(
            (media for media in self._media_list if media.type == GraphQLMediaType.VIDEO),
            None,
        )
        if not video_item:
            return None
        return VideoData(
            url=video_item.url,
            thumbnail_url=video_item.thumbnail_url,
            duration=video_item.duration,
            width=video_item.width,
            height=video_item.height,
        )

    @strawberry.field
    def likes(self) -> int:
        return self.likes_count

    @strawberry.field
    def comments(self) -> int:
        return self.comments_count

    @strawberry.field(name="likeCount")
    def like_count(self) -> int:
        return self.likes_count

    @strawberry.field(name="likedImage")
    def liked_image(self) -> int | None:
        # Kept for backward compatibility.
        # Mobile dev: migrate to viewerState.liked for per-user like state.
        return 1 if self.likes_count else None

    @strawberry.field(name="viewerState")
    def viewer_state(self) -> CirclePostViewerState | None:
        """Per-user like/pray state, populated from Exists annotations
        set by get_circle_feed and get_circle_post.
        """
        return CirclePostViewerState(
            liked=getattr(self, "_is_liked_by_viewer", False),
            prayed=getattr(self, "_is_prayed_by_viewer", False),
        )

    @strawberry.field(name="savedCount")
    def saved_count(self) -> int:
        return 0

    @strawberry.field(name="sharedCount")
    def shared_count(self) -> int:
        return 0

    @classmethod
    def from_db_model(cls, post) -> "CirclePostType":
        instance = cls(
            id=str(post.id),
            user=CirclePostAuthorType.from_user(post.user),
            created_at=post.created_at,
            text=post.text or None,
            likes_count=post.likes_count,
            comments_count=post.comments_count,
            prayed_count=post.prayed_count,
            anchor_liked_count=post.anchor_liked_count,
            _media_list=[
                _media_file_to_graphql(media_file) for media_file in post.media_files.all()
            ],
        )
        # Carry viewer annotations from the ORM queryset onto the instance
        # so the viewerState resolver can read them without an extra DB call.
        instance._is_liked_by_viewer = getattr(post, "is_liked_by_viewer", False)
        instance._is_prayed_by_viewer = getattr(post, "is_prayed_by_viewer", False)
        return instance


@strawberry.type
class CircleFeedResponse:
    posts: list[CirclePostType]
    page_info: PageInfo = strawberry.field(name="pageInfo")


@strawberry.type
class CircleFeedDataType:
    banner_image: str | None = strawberry.field(name="bannerImage")
    profile_image: str | None = strawberry.field(name="profileImage")
    cover_image: str | None = strawberry.field(name="coverImage")
    suggestion_card_image: str | None = strawberry.field(name="suggestionCardImage")
    name: str
    description: str
    member_count: int = strawberry.field(name="memberCount")
    is_joined: bool = strawberry.field(name="isJoined")
    active_anchor: AnchorType | None = strawberry.field(name="activeAnchor", default=None)
    anchor_dates: list[str] = strawberry.field(name="anchorDates")
    past_anchors: list[AnchorType] = strawberry.field(name="pastAnchors")
    posts: list[CirclePostType]
    member_avatars: list[str] = strawberry.field(name="memberAvatars")
    rules: list[CircleRule]


def _build_circle_feed_response(
    circle_id: str,
    page: int,
    page_size: int,
    viewer_id: str | None = None,
    sort_by: str = "NEW",
    author_id: str | None = None,
    circle_filter: CirclePostFilterEnum | None = None,
) -> CircleFeedResponse:
    sort_by, author_id = _resolve_circle_feed_filters(
        viewer_id=viewer_id,
        sort_by=sort_by,
        author_id=author_id,
        circle_filter=circle_filter,
    )
    if circle_filter == CirclePostFilterEnum.VIEWER_POSTS and not viewer_id:
        return CircleFeedResponse(
            posts=[],
            page_info=PageInfo(
                has_next_page=False,
                total_count=0,
                current_page=page,
            ),
        )

    posts, has_next_page, total_count = get_circle_feed(
        circle_id,
        page,
        page_size,
        viewer_id=viewer_id,
        sort_by=sort_by,
        author_id=author_id,
    )
    return CircleFeedResponse(
        posts=[CirclePostType.from_db_model(p) for p in posts],
        page_info=PageInfo(
            has_next_page=has_next_page,
            total_count=total_count,
            current_page=page,
        ),
    )


def _resolve_circle_feed_filters(
    *,
    viewer_id: str | None,
    sort_by: str,
    author_id: str | None,
    circle_filter: CirclePostFilterEnum | None,
) -> tuple[str, str | None]:
    if circle_filter is None:
        return sort_by, author_id

    if circle_filter == CirclePostFilterEnum.TRENDING:
        return "TRENDING", None

    if circle_filter == CirclePostFilterEnum.VIEWER_POSTS:
        return "NEW", viewer_id

    return "NEW", None


def _circle_rules_for(circle) -> list[CircleRule]:
    from core.circles.models import CircleRule as CircleRuleModel

    scoped_rules = list(circle.rules.all())
    rules = scoped_rules or list(CircleRuleModel.get_default_rules())
    return [
        CircleRule(
            id=rule.rule_number,
            rule_number=rule.rule_number,
            title=rule.title,
            description=rule.description,
        )
        for rule in rules
    ]


@strawberry.type
class LikeCirclePostPayload:
    """Response for likeCirclePost mutation."""

    success: bool
    liked: bool | None = None
    likes_count: int | None = strawberry.field(name="likesCount", default=None)
    error: ErrorType | None = None


@strawberry.type
class CreateCirclePostPayload:
    success: bool
    post: CirclePostType | None = None
    error: ErrorType | None = None


@strawberry.type
class AnchorEngagementPayload:
    success: bool
    prayed: bool | None = None
    liked: bool | None = None
    prayed_count: int | None = strawberry.field(name="prayedCount", default=None)
    anchor_liked_count: int | None = strawberry.field(name="anchorLikedCount", default=None)
    error: ErrorType | None = None


@strawberry.type
class CirclePostEngagementPayload:
    success: bool
    prayed: bool | None = None
    prayed_count: int | None = strawberry.field(name="prayedCount", default=None)
    error: ErrorType | None = None


# ──────────────────────────────────────────────
#  Queries
# ──────────────────────────────────────────────


@strawberry.type
class CircleQueries:
    @strawberry.field(name="allCircles")
    def all_circles(
        self, info: Info, limit: int = 20, cursor: str | None = None
    ) -> list[CircleType]:
        viewer_id = _get_authenticated_user_id(info)
        circles = get_all_circles(viewer_id, limit, cursor)
        return [CircleType.from_db_model(c) for c in circles]

    @strawberry.field(name="myCircles")
    def my_circles(self, info: Info, limit: int = 20) -> list[CircleType]:
        viewer_id = _get_authenticated_user_id(info)
        if not viewer_id:
            return []
        circles = get_my_circles(viewer_id, limit)
        return [CircleType.from_db_model(c) for c in circles]

    @strawberry.field(name="suggestedCircles")
    def suggested_circles(self, info: Info, limit: int = 10) -> list[CircleType]:
        viewer_id = _get_authenticated_user_id(info)
        circles = get_suggested_circles(viewer_id, limit)
        return [CircleType.from_db_model(c) for c in circles]

    @strawberry.field
    def circle(self, info: Info, id: str) -> CircleType | None:
        viewer_id = _get_authenticated_user_id(info)
        circle = get_circle_by_id(id, viewer_id=viewer_id)
        return CircleType.from_db_model(circle)

    @strawberry.field(name="activeAnchor")
    def active_anchor(self, info: Info, circle_id: str) -> AnchorType | None:
        from core.circles.anchor_services import get_active_anchor

        viewer_id = _get_authenticated_user_id(info)
        anchor = get_active_anchor(circle_id, viewer_id=viewer_id)
        return AnchorType.from_db_model(anchor)

    @strawberry.field(name="anchorHistory")
    def anchor_history(
        self,
        info: Info,
        circle_id: str,
        limit: int = 20,
        cursor: str | None = None,
        include_active: bool = True,
    ) -> list[AnchorType]:
        from core.circles.anchor_services import get_anchor_history

        viewer_id = _get_authenticated_user_id(info)
        anchors = get_anchor_history(
            circle_id,
            limit,
            cursor,
            include_active=include_active,
            viewer_id=viewer_id,
        )
        return [AnchorType.from_db_model(a) for a in anchors]

    @strawberry.field(name="circleFeed")
    def circle_feed(
        self,
        info: Info,
        circle_id: str,
        page: int = 1,
        page_size: int = 20,
        sort_by: str = "NEW",
        author_id: str | None = None,
        circle_filter: CirclePostFilterEnum | None = None,
    ) -> CircleFeedResponse:
        viewer_id = _get_authenticated_user_id(info)
        return _build_circle_feed_response(
            circle_id,
            page,
            page_size,
            viewer_id,
            sort_by=sort_by,
            author_id=author_id,
            circle_filter=circle_filter,
        )

    @strawberry.field(name="circlePosts")
    def circle_posts(
        self,
        info: Info,
        circle_id: str,
        page: int = 1,
        page_size: int = 20,
        sort_by: str = "NEW",
        author_id: str | None = None,
        circle_filter: CirclePostFilterEnum | None = None,
    ) -> CircleFeedResponse:
        viewer_id = _get_authenticated_user_id(info)
        return _build_circle_feed_response(
            circle_id,
            page,
            page_size,
            viewer_id,
            sort_by=sort_by,
            author_id=author_id,
            circle_filter=circle_filter,
        )

    @strawberry.field(
        name="circlePost",
        description="Fetch a single CirclePost by ID. Use this for post detail screens.",
    )
    def circle_post(self, info: Info, id: str) -> CirclePostType | None:
        from core.shared.exceptions import ZionaError

        viewer_id = _get_authenticated_user_id(info)
        try:
            post = get_circle_post(post_id=id, viewer_id=viewer_id)
            return CirclePostType.from_db_model(post)
        except ZionaError:
            return None

    @strawberry.field(
        name="circlePostComments",
        description="Paginated inline comments for a CirclePost, with viewer like state.",
    )
    def circle_post_comments(
        self,
        info: Info,
        post_id: str,
        page: int = 1,
        page_size: int = 30,
    ) -> "CirclePostCommentsResponse":
        from core.circles.comment_services import get_circle_post_comments

        viewer_id = _get_authenticated_user_id(info)
        comments, has_next_page, total_count = get_circle_post_comments(
            post_id=post_id,
            viewer_id=viewer_id,
            page=page,
            page_size=page_size,
        )
        return CirclePostCommentsResponse(
            comments=[CirclePostCommentType.from_db_model(c) for c in comments],
            page_info=PageInfo(
                has_next_page=has_next_page,
                total_count=total_count,
                current_page=page,
            ),
        )

    @strawberry.field(
        name="anchor",
        description="Fetch a single Anchor by ID. Use this for deep-link/push-notification screens.",
    )
    def anchor_by_id(self, info: Info, id: str) -> AnchorType | None:
        from core.circles.anchor_services import get_anchor_by_id
        from core.shared.exceptions import ZionaError

        try:
            anchor = get_anchor_by_id(anchor_id=id, viewer_id=_get_authenticated_user_id(info))
            return AnchorType.from_db_model(anchor)
        except ZionaError:
            return None

    @strawberry.field(name="circleFeedData")
    def circle_feed_data(
        self,
        info: Info,
        circle_id: str,
        page: int = 1,
        page_size: int = 20,
        history_limit: int = 5,
        sort_by: str = "NEW",
        author_id: str | None = None,
        circle_filter: CirclePostFilterEnum | None = None,
    ) -> CircleFeedDataType | None:
        from core.circles.anchor_services import get_active_anchor, get_anchor_history

        viewer_id = _get_authenticated_user_id(info)
        circle = get_circle_by_id(circle_id, viewer_id=viewer_id)
        if not circle:
            return None
        sort_by, author_id = _resolve_circle_feed_filters(
            viewer_id=viewer_id,
            sort_by=sort_by,
            author_id=author_id,
            circle_filter=circle_filter,
        )
        if circle_filter == CirclePostFilterEnum.VIEWER_POSTS and not viewer_id:
            posts = []
        else:
            posts, _, _ = get_circle_feed(
                circle_id,
                page,
                page_size,
                viewer_id=viewer_id,
                sort_by=sort_by,
                author_id=author_id,
            )
        # Only return anchors within the 5-day window so the mobile app
        # naturally stops showing older ones once the purge task removes them.
        past_anchors = get_anchor_history(
            circle_id,
            limit=history_limit,
            include_active=False,
            max_age_days=5,
            viewer_id=viewer_id,
        )
        active_anchor = get_active_anchor(circle_id, viewer_id=viewer_id)
        circle_type = CircleType.from_db_model(circle)
        return CircleFeedDataType(
            banner_image=circle_type.banner_image(),
            profile_image=circle.profile_image_url or None,
            cover_image=circle.cover_image or None,
            suggestion_card_image=circle.cover_image or None,
            name=circle.name,
            description=circle.description,
            member_count=circle.display_member_count
            if circle.display_member_count is not None
            else circle.get_member_count(),
            is_joined=circle.is_user_subscribed(viewer_id),
            active_anchor=AnchorType.from_db_model(active_anchor),
            anchor_dates=_unique_anchor_dates(active_anchor, past_anchors),
            past_anchors=[AnchorType.from_db_model(anchor) for anchor in past_anchors],
            posts=[CirclePostType.from_db_model(post) for post in posts],
            member_avatars=circle_type.member_avatars(),
            rules=_circle_rules_for(circle),
        )

    @strawberry.field(name="anchorByDate")
    def anchor_by_date(self, info: Info, circle_id: str, date: str) -> AnchorType | None:
        from datetime import date as date_type

        from core.circles.anchor_services import get_anchor_by_date

        try:
            parsed_date = date_type.fromisoformat(date)
        except ValueError:
            return None
        anchor = get_anchor_by_date(
            circle_id,
            parsed_date,
            viewer_id=_get_authenticated_user_id(info),
        )
        return AnchorType.from_db_model(anchor)

    @strawberry.field(name="anchorResponses")
    def anchor_responses(
        self,
        info: Info,
        anchor_id: str,
        sort: str = "TRENDING",
        my_posts_only: bool = False,
        limit: int = 50,
        cursor: str | None = None,
    ) -> list["AnchorResponseType"]:
        viewer_id = _get_authenticated_user_id(info)
        from core.circles.response_services import get_anchor_responses

        responses = get_anchor_responses(
            anchor_id=anchor_id,
            viewer_id=viewer_id,
            sort=sort,
            my_posts_only=my_posts_only,
            limit=limit,
            cursor=cursor,
        )
        return [AnchorResponseType.from_db_model(r) for r in responses]

    @strawberry.field(name="responseReplies")
    def response_replies(
        self, info: Info, response_id: str, limit: int = 50
    ) -> list["AnchorResponseType"]:
        viewer_id = _get_authenticated_user_id(info)
        from core.circles.response_services import get_response_replies

        replies = get_response_replies(response_id=response_id, viewer_id=viewer_id, limit=limit)
        return [AnchorResponseType.from_db_model(r) for r in replies]


# ──────────────────────────────────────────────
#  Mutations
# ──────────────────────────────────────────────

# ──────────────────────────────────────────────
#  Phase 3 & 4 Enums
# ──────────────────────────────────────────────


@strawberry.enum
class ResponseTypeEnum(enum.Enum):
    REFLECTION = "reflection"
    PRAYER = "prayer"
    QUESTION = "question"
    REPLY = "reply"


@strawberry.enum
class ReactionTypeEnum(enum.Enum):
    AMEN = "amen"
    ENCOURAGED = "encouraged"
    THOUGHTFUL = "thoughtful"


@strawberry.enum
class ResponseSortEnum(enum.Enum):
    RECENT = "RECENT"
    TRENDING = "TRENDING"


# ──────────────────────────────────────────────
#  Phase 3 & 4 Types
# ──────────────────────────────────────────────


@strawberry.type
class AnchorResponseReactionType:
    id: str
    user: UserType
    reaction_type: str = strawberry.field(name="reactionType")
    created_at: datetime = strawberry.field(name="createdAt")


@strawberry.type
class AnchorResponseType:
    id: str
    content: str

    @strawberry.field(name="responseType")
    def response_type(self) -> str:
        return self._dto.response_type

    @strawberry.field(name="mediaUrl")
    def media_url(self) -> str | None:
        return self._dto.media_url or None

    @strawberry.field(name="mediaType")
    def media_type(self) -> str | None:
        return self._dto.media_type or None

    @strawberry.field(name="reactionCount")
    def reaction_count(self) -> int:
        return self._dto.reaction_count

    @strawberry.field(name="createdAt")
    def created_at(self) -> datetime:
        return self._dto.created_at

    @strawberry.field
    def author(self) -> UserType:
        return UserType.from_db_model(self._dto.user)

    @strawberry.field(name="replyCount")
    def reply_count(self) -> int:
        return self._dto.replies.filter(deleted_at__isnull=True).count()

    @strawberry.field(name="viewerReactionType")
    def viewer_reaction_type(self) -> str | None:
        # Populated by annotate in `get_anchor_responses`
        if hasattr(self._dto, "viewer_reaction_type"):
            return self._dto.viewer_reaction_type
        return None

    @classmethod
    def from_db_model(cls, instance):
        if not instance:
            return None
        obj = cls(
            id=str(instance.id),
            content=instance.content or "",
        )
        obj._dto = instance
        return obj


@strawberry.type
class AnchorResponsePayload:
    success: bool
    response: AnchorResponseType | None = None
    error: ErrorType | None = None


@strawberry.type
class ReactionPayload:
    success: bool
    reaction: AnchorResponseReactionType | None = None
    error: ErrorType | None = None


@strawberry.type
class CircleReportPayload:
    success: bool
    error: ErrorType | None = None


@strawberry.type
class CircleMutations:
    @strawberry.mutation(name="joinCircle")
    def join_circle(self, info: Info, circle_id: str) -> JoinCirclePayload:
        viewer_id = _get_authenticated_user_id(info)
        if not viewer_id:
            return JoinCirclePayload(
                success=False,
                error=ErrorType(
                    code="UNAUTHORIZED", message="You must be logged in to join a Circle"
                ),
            )

        try:
            membership = join_circle(viewer_id, circle_id)
            circle = membership.circle
            circle._is_viewer_subscribed = True
            return JoinCirclePayload(success=True, circle=CircleType.from_db_model(circle))
        except ZionaError as e:
            return JoinCirclePayload(success=False, error=ErrorType(code=e.code, message=e.message))

    @strawberry.mutation(name="leaveCircle")
    def leave_circle(self, info: Info, circle_id: str) -> JoinCirclePayload:
        viewer_id = _get_authenticated_user_id(info)
        if not viewer_id:
            return JoinCirclePayload(
                success=False,
                error=ErrorType(
                    code="UNAUTHORIZED", message="You must be logged in to leave a Circle"
                ),
            )

        try:
            leave_circle(viewer_id, circle_id)
            return JoinCirclePayload(success=True)
        except ZionaError as e:
            return JoinCirclePayload(success=False, error=ErrorType(code=e.code, message=e.message))

    @strawberry.mutation(name="createAnchor")
    def create_anchor(
        self,
        info: Info,
        circle_id: str,
        anchor_type: str,
        title: str,
        content: str = "",
        published_at: str | None = None,
        scripture_book: str = "",
        scripture_chapter: int | None = None,
        scripture_verse_start: int | None = None,
        scripture_verse_end: int | None = None,
        scripture_translation: str = "KJV",
        scripture_text: str = "",
        media_url: str = "",
        anchor_text: str = "",
        anchor_verse: str = "",
        background_colors: list[str] | None = None,
        background_image: str = "",
        anchor_image: str = "",
        anchor_video: str = "",
        anchor_image_text: str = "",
        anchor_thumbnail: str = "",
    ) -> CreateAnchorPayload:
        viewer_id = _get_authenticated_user_id(info)
        if not viewer_id:
            return CreateAnchorPayload(
                success=False,
                error=ErrorType(
                    code="UNAUTHORIZED", message="You must be logged in to create an anchor"
                ),
            )

        try:
            from datetime import datetime as dt

            from core.circles.anchor_services import create_anchor as _create_anchor

            parsed_published_at = None
            if published_at:
                parsed_published_at = dt.fromisoformat(published_at)

            anchor = _create_anchor(
                creator_id=viewer_id,
                circle_id=circle_id,
                anchor_type=anchor_type,
                title=title,
                content=content,
                published_at=parsed_published_at,
                scripture_book=scripture_book,
                scripture_chapter=scripture_chapter,
                scripture_verse_start=scripture_verse_start,
                scripture_verse_end=scripture_verse_end,
                scripture_translation=scripture_translation,
                scripture_text=scripture_text,
                media_url=media_url,
                anchor_text=anchor_text,
                anchor_verse=anchor_verse,
                background_colors=background_colors,
                background_image=background_image,
                anchor_image=anchor_image,
                anchor_video=anchor_video,
                anchor_image_text=anchor_image_text,
                anchor_thumbnail=anchor_thumbnail,
            )
            return CreateAnchorPayload(success=True, anchor=AnchorType.from_db_model(anchor))
        except ZionaError as e:
            return CreateAnchorPayload(
                success=False, error=ErrorType(code=e.code, message=e.message)
            )

    @strawberry.mutation(name="respondToAnchor")
    def respond_to_anchor(
        self,
        info: Info,
        anchor_id: str,
        response_type: str,
        content: str,
        media_url: str = "",
        media_type: str = "",
    ) -> AnchorResponsePayload:
        viewer_id = _get_authenticated_user_id(info)
        if not viewer_id:
            return AnchorResponsePayload(
                success=False, error=ErrorType(code="UNAUTHORIZED", message="Login required")
            )
        try:
            from core.circles.response_services import create_response

            response = create_response(
                user_id=viewer_id,
                anchor_id=anchor_id,
                response_type=response_type,
                content=content,
                media_url=media_url,
                media_type=media_type,
            )
            return AnchorResponsePayload(
                success=True, response=AnchorResponseType.from_db_model(response)
            )
        except ZionaError as e:
            return AnchorResponsePayload(
                success=False, error=ErrorType(code=e.code, message=e.message)
            )

    @strawberry.mutation(name="replyToResponse")
    def reply_to_response(
        self,
        info: Info,
        parent_response_id: str,
        content: str,
        media_url: str = "",
        media_type: str = "",
    ) -> AnchorResponsePayload:
        viewer_id = _get_authenticated_user_id(info)
        if not viewer_id:
            return AnchorResponsePayload(
                success=False, error=ErrorType(code="UNAUTHORIZED", message="Login required")
            )
        try:
            from core.circles.response_services import create_reply

            reply = create_reply(
                user_id=viewer_id,
                parent_response_id=parent_response_id,
                content=content,
                media_url=media_url,
                media_type=media_type,
            )
            return AnchorResponsePayload(
                success=True, response=AnchorResponseType.from_db_model(reply)
            )
        except ZionaError as e:
            return AnchorResponsePayload(
                success=False, error=ErrorType(code=e.code, message=e.message)
            )

    @strawberry.mutation(name="reactToResponse")
    def react_to_response(
        self, info: Info, response_id: str, reaction_type: str
    ) -> ReactionPayload:
        viewer_id = _get_authenticated_user_id(info)
        if not viewer_id:
            return ReactionPayload(
                success=False, error=ErrorType(code="UNAUTHORIZED", message="Login required")
            )
        try:
            from core.circles.response_services import toggle_reaction

            reaction = toggle_reaction(
                user_id=viewer_id, response_id=response_id, reaction_type=reaction_type
            )
            if reaction:
                reaction_obj = AnchorResponseReactionType(
                    id=str(reaction.id),
                    user=UserType.from_db_model(reaction.user),
                    reaction_type=reaction.reaction_type,
                    created_at=reaction.created_at,
                )
                return ReactionPayload(success=True, reaction=reaction_obj)
            return ReactionPayload(success=True, reaction=None)  # Toggled off
        except ZionaError as e:
            return ReactionPayload(success=False, error=ErrorType(code=e.code, message=e.message))

    @strawberry.mutation(name="reportCircleContent")
    def report_circle_content(
        self, info: Info, circle_id: str, target_type: str, target_id: str, reason: str
    ) -> CircleReportPayload:
        viewer_id = _get_authenticated_user_id(info)
        if not viewer_id:
            return CircleReportPayload(
                success=False, error=ErrorType(code="UNAUTHORIZED", message="Login required")
            )
        try:
            from core.circles.moderation_services import report_circle_content as do_report

            do_report(
                reporter_id=viewer_id,
                circle_id=circle_id,
                target_type=target_type,
                target_id=target_id,
                reason=reason,
            )
            return CircleReportPayload(success=True)
        except ZionaError as e:
            return CircleReportPayload(
                success=False, error=ErrorType(code=e.code, message=e.message)
            )

    @strawberry.mutation(name="createCirclePost")
    def create_circle_post(
        self,
        info: Info,
        circle_id: str,
        text: str | None = None,
        media_ids: list[str] | None = None,
        media_urls: list[str] | None = None,
        media_type: GraphQLMediaType | None = None,
        thumbnail_url: str | None = None,
        width: int | None = None,
        height: int | None = None,
        duration: int | None = None,
    ) -> CreateCirclePostPayload:
        viewer_id = _get_authenticated_user_id(info)
        if not viewer_id:
            return CreateCirclePostPayload(
                success=False,
                error=ErrorType(code="UNAUTHORIZED", message="Login required"),
            )
        try:
            post = create_circle_post(
                user_id=viewer_id,
                circle_id=circle_id,
                text=text or "",
                media_ids=media_ids,
                media_urls=media_urls,
                media_type=media_type.value if media_type else None,
                thumbnail_url=thumbnail_url,
                width=width,
                height=height,
                duration=duration,
            )
            return CreateCirclePostPayload(success=True, post=CirclePostType.from_db_model(post))
        except ZionaError as e:
            return CreateCirclePostPayload(
                success=False, error=ErrorType(code=e.code, message=e.message)
            )

    @strawberry.mutation(name="prayForAnchor")
    def pray_for_anchor(self, info: Info, anchor_id: str) -> AnchorEngagementPayload:
        viewer_id = _get_authenticated_user_id(info)
        if not viewer_id:
            return AnchorEngagementPayload(
                success=False,
                error=ErrorType(code="UNAUTHORIZED", message="Login required"),
            )
        try:
            result = pray_for_anchor(user_id=viewer_id, anchor_id=anchor_id)
            return AnchorEngagementPayload(
                success=True,
                prayed=result["prayed"],
                prayed_count=result["prayed_count"],
            )
        except ZionaError as e:
            return AnchorEngagementPayload(
                success=False, error=ErrorType(code=e.code, message=e.message)
            )

    @strawberry.mutation(name="likeAnchor")
    def like_anchor(self, info: Info, anchor_id: str) -> AnchorEngagementPayload:
        viewer_id = _get_authenticated_user_id(info)
        if not viewer_id:
            return AnchorEngagementPayload(
                success=False,
                error=ErrorType(code="UNAUTHORIZED", message="Login required"),
            )
        try:
            result = like_anchor(user_id=viewer_id, anchor_id=anchor_id)
            return AnchorEngagementPayload(
                success=True,
                liked=result["liked"],
                anchor_liked_count=result["anchor_liked_count"],
            )
        except ZionaError as e:
            return AnchorEngagementPayload(
                success=False, error=ErrorType(code=e.code, message=e.message)
            )

    @strawberry.mutation(name="prayForCirclePost")
    def pray_for_circle_post(self, info: Info, post_id: str) -> CirclePostEngagementPayload:
        viewer_id = _get_authenticated_user_id(info)
        if not viewer_id:
            return CirclePostEngagementPayload(
                success=False,
                error=ErrorType(code="UNAUTHORIZED", message="Login required"),
            )
        try:
            result = pray_for_circle_post(user_id=viewer_id, post_id=post_id)
            return CirclePostEngagementPayload(
                success=True,
                prayed=result["prayed"],
                prayed_count=result["prayed_count"],
            )
        except ZionaError as e:
            return CirclePostEngagementPayload(
                success=False, error=ErrorType(code=e.code, message=e.message)
            )

    @strawberry.mutation(
        name="likeCirclePost",
        description="Toggle a like on a CirclePost. Returns the new like state and count.",
    )
    def like_circle_post(self, info: Info, post_id: str) -> LikeCirclePostPayload:
        viewer_id = _get_authenticated_user_id(info)
        if not viewer_id:
            return LikeCirclePostPayload(
                success=False,
                error=ErrorType(code="UNAUTHORIZED", message="Login required"),
            )
        try:
            result = like_circle_post(user_id=viewer_id, post_id=post_id)
            return LikeCirclePostPayload(
                success=True,
                liked=result["liked"],
                likes_count=result["likes_count"],
            )
        except ZionaError as e:
            return LikeCirclePostPayload(
                success=False, error=ErrorType(code=e.code, message=e.message)
            )

    @strawberry.mutation(
        name="ensureCirclePostLiked",
        description="Idempotently like a CirclePost. Repeated calls keep it liked.",
    )
    def ensure_circle_post_liked(self, info: Info, post_id: str) -> LikeCirclePostPayload:
        viewer_id = _get_authenticated_user_id(info)
        if not viewer_id:
            return LikeCirclePostPayload(
                success=False,
                error=ErrorType(code="UNAUTHORIZED", message="Login required"),
            )
        try:
            result = ensure_circle_post_liked(user_id=viewer_id, post_id=post_id)
            return LikeCirclePostPayload(
                success=True,
                liked=result["liked"],
                likes_count=result["likes_count"],
            )
        except ZionaError as e:
            return LikeCirclePostPayload(
                success=False, error=ErrorType(code=e.code, message=e.message)
            )

    # ── Phase 6: Circle Post Comment mutations ─────────────────────────────

    @strawberry.mutation(
        name="commentOnCirclePost",
        description="Add an inline comment to a CirclePost.",
    )
    def comment_on_circle_post(
        self, info: Info, post_id: str, text: str
    ) -> "CirclePostCommentPayload":
        viewer_id = _get_authenticated_user_id(info)
        if not viewer_id:
            return CirclePostCommentPayload(
                success=False, error=ErrorType(code="UNAUTHORIZED", message="Login required")
            )
        try:
            from core.circles.comment_services import create_circle_post_comment

            comment = create_circle_post_comment(user_id=viewer_id, post_id=post_id, text=text)
            return CirclePostCommentPayload(
                success=True, comment=CirclePostCommentType.from_db_model(comment)
            )
        except ZionaError as e:
            return CirclePostCommentPayload(
                success=False, error=ErrorType(code=e.code, message=e.message)
            )

    @strawberry.mutation(
        name="deleteCirclePostComment",
        description="Soft-delete your own comment on a CirclePost.",
    )
    def delete_circle_post_comment(self, info: Info, comment_id: str) -> "CirclePostCommentPayload":
        viewer_id = _get_authenticated_user_id(info)
        if not viewer_id:
            return CirclePostCommentPayload(
                success=False, error=ErrorType(code="UNAUTHORIZED", message="Login required")
            )
        try:
            from core.circles.comment_services import delete_circle_post_comment

            delete_circle_post_comment(user_id=viewer_id, comment_id=comment_id)
            return CirclePostCommentPayload(success=True)
        except ZionaError as e:
            return CirclePostCommentPayload(
                success=False, error=ErrorType(code=e.code, message=e.message)
            )

    @strawberry.mutation(
        name="likeCirclePostComment",
        description="Toggle a like on a CirclePost comment. Returns new like state and count.",
    )
    def like_circle_post_comment(
        self, info: Info, comment_id: str
    ) -> "CirclePostCommentLikePayload":
        viewer_id = _get_authenticated_user_id(info)
        if not viewer_id:
            return CirclePostCommentLikePayload(
                success=False, error=ErrorType(code="UNAUTHORIZED", message="Login required")
            )
        try:
            from core.circles.comment_services import toggle_circle_post_comment_like

            liked, likes_count = toggle_circle_post_comment_like(
                user_id=viewer_id, comment_id=comment_id
            )
            return CirclePostCommentLikePayload(success=True, liked=liked, likes_count=likes_count)
        except ZionaError as e:
            return CirclePostCommentLikePayload(
                success=False, error=ErrorType(code=e.code, message=e.message)
            )


# ══════════════════════════════════════════════════════════════════════════════
#  Phase 6: Circle Post Comment — GraphQL Types, Query, Payloads
# ══════════════════════════════════════════════════════════════════════════════


@strawberry.type
class CirclePostCommentViewerState:
    """Per-viewer like state on a single comment."""

    liked: bool = False


@strawberry.type
class CirclePostCommentType:
    """A single inline comment on a CirclePost."""

    id: str
    text: str
    created_at: datetime = strawberry.field(name="createdAt")
    updated_at: datetime = strawberry.field(name="updatedAt")
    likes_count: int = strawberry.field(name="likesCount", default=0)

    @strawberry.field
    def author(self) -> CirclePostAuthorType:
        return CirclePostAuthorType.from_user(self._dto.user)

    @strawberry.field(name="viewerState")
    def viewer_state(self) -> CirclePostCommentViewerState:
        """Like state for the authenticated viewer.

        Populated from the is_liked_by_viewer annotation set by
        get_circle_post_comments — zero extra DB queries.
        """
        return CirclePostCommentViewerState(liked=getattr(self._dto, "is_liked_by_viewer", False))

    @classmethod
    def from_db_model(cls, comment) -> "CirclePostCommentType":
        instance = cls(
            id=str(comment.id),
            text=comment.text,
            created_at=comment.created_at,
            updated_at=comment.updated_at,
            likes_count=comment.likes_count,
        )
        instance._dto = comment
        return instance


@strawberry.type
class CirclePostCommentsResponse:
    """Paginated response for circlePostComments query."""

    comments: list[CirclePostCommentType]
    page_info: PageInfo = strawberry.field(name="pageInfo")


@strawberry.type
class CirclePostCommentPayload:
    """Response for comment create / delete mutations."""

    success: bool
    comment: CirclePostCommentType | None = None
    error: ErrorType | None = None


@strawberry.type
class CirclePostCommentLikePayload:
    """Response for likeCirclePostComment mutation."""

    success: bool
    liked: bool | None = None
    likes_count: int | None = strawberry.field(name="likesCount", default=None)
    error: ErrorType | None = None

"""Circle post, feed, engagement-payload and comment GraphQL types.

Split from the former core/circles/schema.py (no contract change).
"""

import dataclasses
import enum
from datetime import datetime

import strawberry

from core.circles.schema._helpers import _media_file_to_graphql
from core.circles.schema.anchors import AnchorType
from core.circles.schema.circles import CircleRule
from core.circles.services import (
    get_circle_feed,
)
from core.feed.schema import ImageData, VideoData
from core.media.schema import MediaFileType
from core.shared.types import ErrorType, PageInfo
from core.shared.types import MediaType as GraphQLMediaType


@strawberry.enum
class CirclePostFilterEnum(enum.Enum):
    NEW = "NEW"
    TRENDING = "TRENDING"
    VIEWER_POSTS = "VIEWER_POSTS"


@strawberry.type
class CirclePostViewerState:
    """Viewer's engagement state on a CirclePost."""

    liked: bool = False
    prayed: bool = False


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

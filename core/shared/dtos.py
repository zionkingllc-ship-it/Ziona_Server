"""
Data Transfer Objects for the Ziona platform.

All DTOs use Pydantic v2 with camelCase serialization to match
the mobile TypeScript specification exactly.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


def _to_camel(field_name: str) -> str:
    """Convert snake_case to camelCase."""
    parts = field_name.split("_")
    return parts[0] + "".join(word.capitalize() for word in parts[1:])


class CamelCaseModel(BaseModel):
    """Base model with camelCase JSON serialization."""

    model_config = ConfigDict(
        alias_generator=_to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class AuthorDTO(CamelCaseModel):
    """Lightweight user representation for embedded contexts."""

    id: str
    username: str
    avatar_url: str | None = None


class MediaItemDTO(CamelCaseModel):
    """Single image item within an image post."""

    id: str
    url: str
    width: int | None = None
    height: int | None = None
    order: int


class ImageMediaDTO(CamelCaseModel):
    """Media payload for image posts."""

    type: Literal["image"] = "image"
    items: list[MediaItemDTO]


class VideoMediaDTO(CamelCaseModel):
    """Media payload for video posts."""

    type: Literal["video"] = "video"
    url: str
    thumbnail_url: str
    duration: int
    width: int | None = None
    height: int | None = None


class TextMediaDTO(CamelCaseModel):
    """Media payload for text posts."""

    type: Literal["text"] = "text"
    background_image: str = ""


class StatsDTO(CamelCaseModel):
    """Engagement statistics for a post."""

    likes_count: int = 0
    comments_count: int = 0
    shares_count: int = 0
    saves_count: int = 0


class ViewerStateDTO(CamelCaseModel):
    """Current viewer's relationship to a post."""

    liked: bool = False
    saved: bool = False
    following_author: bool = False
    followed_by_author: bool = False
    is_owner: bool = False


class PostResponseDTO(CamelCaseModel):
    """Canonical post response matching mobile TypeScript spec."""

    id: str
    type: Literal["video", "image", "text", "bible"]
    created_at: str  # ISO 8601
    caption: str | None = None
    category_id: str | None = None
    author: AuthorDTO
    media: ImageMediaDTO | VideoMediaDTO | TextMediaDTO
    stats: StatsDTO
    viewer_state: ViewerStateDTO | None = None
    share_url: str
    scripture: ScriptureDTO | None = None


class ScriptureVerseDTO(CamelCaseModel):
    """A single verse inside a scripture block."""

    number: int
    text: str


class ScriptureDTO(CamelCaseModel):
    """Scripture verse attached to a post."""

    reference: str
    text: str
    version: str = "KJV"
    book: str
    chapter: int
    verse_start: int
    verse_end: int | None = None
    verses: list[ScriptureVerseDTO] = []


PostResponseDTO.model_rebuild()


class UserSuggestionDTO(CamelCaseModel):
    """Suggested user for empty feed states."""

    id: str
    username: str
    avatar_url: str | None = None
    bio: str | None = None
    followers_count: int = 0


class EmptyStateDTO(CamelCaseModel):
    """Empty state info for feeds with no content."""

    message: str
    suggestions: list[UserSuggestionDTO] = []


class FeedResponseDTO(CamelCaseModel):
    """Response for For You and Following feeds."""

    posts: list[PostResponseDTO] = []
    next_cursor: str | None = None
    has_more: bool = False
    empty_state: EmptyStateDTO | None = None


class CommentStatsDTO(CamelCaseModel):
    """Engagement statistics for a comment."""

    likes_count: int = 0
    replies_count: int = 0


class CommentViewerStateDTO(CamelCaseModel):
    """Current viewer's relationship to a comment."""

    liked: bool = False
    is_owner: bool = False


class CommentResponseDTO(CamelCaseModel):
    """Single comment response."""

    id: str
    post_id: str
    parent_comment_id: str | None = None
    user: AuthorDTO
    text: str
    stats: CommentStatsDTO
    viewer_state: CommentViewerStateDTO | None = None
    created_at: str
    # Inline reply preview (first 3 replies only).
    # Use the `commentReplies` query for full paginated replies.
    replies: list[CommentResponseDTO] = []


# Required to resolve the self-referential `replies` annotation.
CommentResponseDTO.model_rebuild()


class CommentsResponseDTO(CamelCaseModel):
    """Paginated comments list response."""

    comments: list[CommentResponseDTO] = []
    next_cursor: str | None = None
    has_more: bool = False
    total_count: int = 0


class LikeResponseDTO(CamelCaseModel):
    """Response after like/unlike action."""

    success: bool
    liked: bool
    likes_count: int = 0


class SaveResponseDTO(CamelCaseModel):
    """Response after save/unsave action."""

    success: bool
    saved: bool
    saves_count: int = 0


class BookmarkFolderDTO(CamelCaseModel):
    """Bookmark folder representation."""

    id: str
    name: str
    saved_count: int = 0
    created_at: str = ""


class BookmarkFoldersResponseDTO(CamelCaseModel):
    """List of bookmark folders."""

    folders: list[BookmarkFolderDTO] = []
    total_saved_posts: int = 0


class SavedPostsResponseDTO(CamelCaseModel):
    """Paginated saved posts within a folder."""

    folder_id: str | None = None
    folder_name: str = "All"
    posts: list[PostResponseDTO] = []
    next_cursor: str | None = None
    has_more: bool = False


class ShareFriendDTO(CamelCaseModel):
    """Friend entry for share target list."""

    id: str
    username: str
    avatar_url: str | None = None


class ShareFriendsResponseDTO(CamelCaseModel):
    """List of friends for sharing."""

    friends: list[ShareFriendDTO] = []
    total_count: int = 0


class ShareDirectResponseDTO(CamelCaseModel):
    """Response after sharing post to a user."""

    success: bool
    message: str


class ShareExternalResponseDTO(CamelCaseModel):
    """Response after generating external share link."""

    share_url: str


class ShareResponseDTO(CamelCaseModel):
    """Response for share operations (direct or external)."""

    success: bool
    share_id: str | None = None
    share_type: str = ""
    share_url: str | None = None


class UserSearchResultDTO(CamelCaseModel):
    """Search result for a user."""

    id: str
    username: str
    full_name: str = ""
    avatar_url: str | None = None
    bio: str = ""
    is_following: bool = False


class UserStatsDTO(CamelCaseModel):
    """User profile statistics."""

    posts_count: int = 0
    followers_count: int = 0
    following_count: int = 0


class ProfileViewerStateDTO(CamelCaseModel):
    """Viewer's relationship to a profile."""

    is_following: bool = False
    is_owner: bool = False
    is_following_back: bool = False


class UserProfileStatsDTO(CamelCaseModel):
    """User profile statistics (flattened)."""

    followers_count: int = 0
    following_count: int = 0
    posts_count: int = 0


class UserProfileDTO(CamelCaseModel):
    """Full user profile response."""

    id: str
    username: str
    full_name: str = ""
    bio: str = ""
    avatar_url: str | None = None
    location: str = ""
    hide_like_count: bool = False
    stats: UserProfileStatsDTO
    is_following: bool = False
    is_followed_by: bool = False
    is_own_profile: bool = False
    recent_posts: list[PostResponseDTO] = []
    created_at: str


class FollowResponseDTO(CamelCaseModel):
    """Response after follow/unfollow action."""

    success: bool
    following: bool
    followers_count: int = 0


class FollowUserDTO(CamelCaseModel):
    """User entry in follower/following lists."""

    id: str
    username: str
    avatar_url: str | None = None
    bio: str | None = None
    is_following: bool = False
    is_following_back: bool = False


class FollowersResponseDTO(CamelCaseModel):
    """Paginated followers list."""

    users: list[FollowUserDTO] = []
    next_cursor: str | None = None
    has_more: bool = False
    total_count: int = 0


FollowingResponseDTO = FollowersResponseDTO


class ReportContentResponseDTO(CamelCaseModel):
    """Response after reporting content."""

    success: bool
    message: str


class SetInterestsResponseDTO(CamelCaseModel):
    """Response after setting user interests."""

    success: bool
    interests: list[str] = []

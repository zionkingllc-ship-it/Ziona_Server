"""GraphQL types, queries, and mutations for the profiles domain."""


import strawberry

from core.feed.schema import FeedPost, _dto_to_feed_post
from core.shared.types import ErrorType
from core.users.schema import _get_authenticated_user_id


@strawberry.type
class ProfileStatsType:
    """Profile statistics."""

    followers_count: int = 0
    following_count: int = 0
    posts_count: int = 0


@strawberry.type
class ProfileViewerState:
    """Viewer's relationship to a profile."""

    following_author: bool = False
    is_owner: bool = False


@strawberry.type
class UserProfileType:
    """A user's full profile."""

    id: str
    username: str
    full_name: str
    bio: str
    avatar_url: str | None = None
    location: str
    stats: ProfileStatsType
    viewer_state: ProfileViewerState | None = None
    recent_posts: list[FeedPost]
    created_at: str


@strawberry.type
class ProfilePostResponseListDTO:
    """Paginated list of profile posts."""

    posts: list[FeedPost]
    nextCursor: str | None = None
    hasMore: bool = False


@strawberry.type
class ProfilePayload:
    """
    Response returned when mutating a user's profile contents.

    Contains the fully resolved `UserProfileType` echoing back what exactly changed
    on the backend seamlessly.

    **Authentication:** Required
    **Related operations:** update_profile
    """

    success: bool = strawberry.field(description="Whether the profile updated successfully")
    profile: UserProfileType | None = strawberry.field(
        default=None, description="The updated profile data"
    )
    error: ErrorType | None = strawberry.field(default=None, description="Explicit error info")
    message: str | None = strawberry.field(default=None, description="Success or error message")
    error_code: str | None = strawberry.field(
        default=None, description="Code dictating failure reason safely"
    )


def _dto_to_profile(dto) -> UserProfileType:
    """Convert UserProfileDTO to UserProfileType."""
    return UserProfileType(
        id=dto.id,
        username=dto.username,
        full_name=dto.full_name,
        bio=dto.bio,
        avatar_url=dto.avatar_url,
        location=dto.location,
        stats=ProfileStatsType(
            followers_count=dto.stats.followers_count,
            following_count=dto.stats.following_count,
            posts_count=dto.stats.posts_count,
        ),
        viewer_state=ProfileViewerState(
            following_author=dto.is_following,
            is_owner=dto.is_own_profile,
        ),
        recent_posts=[_dto_to_feed_post(p) for p in dto.recent_posts],
        created_at=dto.created_at,
    )


@strawberry.type
class ProfileQueries:
    """Profile domain GraphQL queries."""

    @strawberry.field(
        description="Retrieve a targeted user's public profile and follower statistics."
    )
    def user_profile(
        self,
        info: strawberry.types.Info,
        user_id: str,
    ) -> UserProfileType | None:
        """
        Get a specific user's public profile data globally safely.

        Dynamically calculates if the `viewer_id` follows the target user, and populates
        the `is_following` flag automatically. Fetches their recent public posts array.

        **Authentication:** Optional (will modify viewer state tracking natively)
        **Parameters:**
        - user_id (String, required) - The UUID corresponding to the targeted user
        **Returns:** Nullable UserProfileType
        **Errors:** Returns None gracefully instead of blowing up the client if blocked/deleted.
        """
        from core.profiles.services import ProfileService
        from core.shared.exceptions import ProfileError

        viewer_id = _get_authenticated_user_id(info)

        try:
            result = ProfileService.get_user_profile(
                target_user_id=user_id,
                viewer_id=viewer_id,
            )
            return _dto_to_profile(result)
        except ProfileError:
            return None

    @strawberry.field(description="Get paginated list of posts authored by the targeted user.")
    def user_posts(
        self,
        info: strawberry.types.Info,
        user_id: str,
        limit: int = 20,
        cursor: str | None = None,
    ) -> ProfilePostResponseListDTO:
        """
        Get chronological cursor paginated feed of posts authored by a user.

        **Authentication:** Optional
        **Parameters:**
        - user_id (String, required) - Target user UUID
        - limit (Int, optional) - Pagination chunk size target
        - cursor (String, optional) - Pass nextCursor backwards
        **Returns:** ProfilePostResponseListDTO with exact posts array
        **Errors:** Returns empty array safely globally natively.
        """
        from core.profiles.services import ProfileService

        viewer_id = _get_authenticated_user_id(info)

        result = ProfileService.get_user_posts(
            user_id=user_id,
            limit=limit,
            cursor=cursor,
            viewer_id=viewer_id,
        )

        posts = [_dto_to_feed_post(p) for p in result["posts"]]

        return ProfilePostResponseListDTO(
            posts=posts,
            nextCursor=result["next_cursor"],
            hasMore=result["has_more"],
        )

    @strawberry.field(description="Get paginated list of posts the targeted user has liked.")
    def liked_posts(
        self,
        info: strawberry.types.Info,
        user_id: str,
        limit: int = 20,
        cursor: str | None = None,
    ) -> ProfilePostResponseListDTO:
        """
        Get chronological cursor paginated feed of posts a user has recently liked.

        Filters for public visibility automatically natively. Re-calculates viewer_state
        dynamically per post based on the requesting `viewer_id`.

        **Authentication:** Optional
        **Parameters:**
        - user_id (String, required) - Remote target to query
        - limit (Int, optional) - Cap bounds (defaults to 20)
        - cursor (String, optional) - Pass nextCursor safely backwards
        **Returns:** ProfilePostResponseListDTO with exact posts array
        **Errors:** Returns empty array safely on error bounding.
        """
        from core.profiles.services import ProfileService

        viewer_id = _get_authenticated_user_id(info)

        result = ProfileService.get_user_liked_posts(
            user_id=user_id,
            limit=limit,
            cursor=cursor,
            viewer_id=viewer_id,
        )

        posts = [_dto_to_feed_post(p) for p in result["posts"]]

        return ProfilePostResponseListDTO(
            posts=posts,
            nextCursor=result["next_cursor"],
            hasMore=result["has_more"],
        )


@strawberry.type
class ProfileMutations:
    """Profile domain GraphQL mutations."""

    @strawberry.mutation(description="Update the authenticated user's public profile information.")
    def update_profile(
        self,
        info: strawberry.types.Info,
        bio: str | None = None,
        full_name: str | None = None,
        avatar_url: str | None = None,
        location: str | None = None,
    ) -> ProfilePayload:
        """
        Modify existing user's profile content fields selectively natively.

        Fields omitted natively are perfectly ignored yielding partial update guarantees.
        Limits bio length internally dynamically to platform norms natively.

        **Authentication:** Required
        **Parameters:**
        - bio (String, optional) - Public profile blurb
        - full_name (String, optional) - Display label
        - avatar_url (String, optional) - Public bucket URL
        - location (String, optional) - Global location label
        **Returns:** ProfilePayload echoing the complete updated entity cleanly
        **Errors:** UNAUTHENTICATED, VALIDATION_ERROR native limits.
        """
        from core.profiles.services import ProfileService
        from core.shared.exceptions import ProfileError

        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return ProfilePayload(
                success=False,
                message="Authentication required",
                error_code="UNAUTHORIZED",
            )

        try:
            result = ProfileService.update_profile(
                user_id=user_id,
                bio=bio,
                full_name=full_name,
                avatar_url=avatar_url,
                location=location,
            )
            return ProfilePayload(
                success=True,
                profile=_dto_to_profile(result),
            )
        except ProfileError as e:
            return ProfilePayload(
                success=False,
                message=e.message,
                error_code=e.code,
                error=ErrorType(code=e.code, message=e.message),
            )

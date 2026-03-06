"""GraphQL types, queries, and mutations for the profiles domain."""


import strawberry

from core.users.schema import _get_authenticated_user_id


@strawberry.type
class ProfileStatsType:
    """Profile statistics."""

    followers_count: int = 0
    following_count: int = 0
    posts_count: int = 0


@strawberry.type
class ProfilePostType:
    """A post within a user profile."""

    id: str
    type: str
    caption: str | None = None
    share_url: str
    created_at: str


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
    is_following: bool = False
    is_own_profile: bool = False
    recent_posts: list[ProfilePostType]
    created_at: str


@strawberry.type
class ProfilePostResponseListDTO:
    """Paginated list of profile posts."""

    posts: list[ProfilePostType]
    nextCursor: str | None = None
    hasMore: bool = False


@strawberry.type
class ProfilePayload:
    """Response for profile mutations."""

    success: bool
    profile: UserProfileType | None = None
    message: str | None = None
    error_code: str | None = None


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
        is_following=dto.is_following,
        is_own_profile=dto.is_own_profile,
        recent_posts=[
            ProfilePostType(
                id=p.id,
                type=p.type,
                caption=p.caption,
                share_url=p.share_url,
                created_at=p.created_at,
            )
            for p in dto.recent_posts
        ],
        created_at=dto.created_at,
    )


@strawberry.type
class ProfileQueries:
    """Profile domain GraphQL queries."""

    @strawberry.field(description="Get a user's profile")
    def user_profile(
        self,
        info: strawberry.types.Info,
        user_id: str,
    ) -> UserProfileType | None:
        """Get a user's profile with stats and viewer state."""
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

    @strawberry.field(description="Get posts the user has liked")
    def liked_posts(
        self,
        info: strawberry.types.Info,
        user_id: str,
        limit: int = 20,
        cursor: str | None = None,
    ) -> ProfilePostResponseListDTO:
        """Get posts a user has liked with pagination."""
        from core.profiles.services import ProfileService

        viewer_id = _get_authenticated_user_id(info)

        result = ProfileService.get_user_liked_posts(
            user_id=user_id,
            limit=limit,
            cursor=cursor,
            viewer_id=viewer_id,
        )

        posts = [
            ProfilePostType(
                id=p.id,
                type=p.type,
                caption=p.caption,
                share_url=p.share_url,
                created_at=p.created_at,
            )
            for p in result["posts"]
        ]

        return ProfilePostResponseListDTO(
            posts=posts,
            nextCursor=result["next_cursor"],
            hasMore=result["has_more"],
        )


@strawberry.type
class ProfileMutations:
    """Profile domain GraphQL mutations."""

    @strawberry.mutation(description="Update the authenticated user's profile")
    def update_profile(
        self,
        info: strawberry.types.Info,
        bio: str | None = None,
        full_name: str | None = None,
        avatar_url: str | None = None,
        location: str | None = None,
    ) -> ProfilePayload:
        """Update profile information."""
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
            )

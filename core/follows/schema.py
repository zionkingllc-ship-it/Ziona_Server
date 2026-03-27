"""GraphQL types, queries, and mutations for the follows domain."""


import strawberry

from core.profiles.schema import ProfileStatsType
from core.shared.types import ErrorType
from core.users.schema import _get_authenticated_user_id


@strawberry.type
class FollowPayload:
    """
    Response outlining state changes when following or unfollowing users.

    **Authentication:** Required
    **Related operations:** follow_user, unfollow_user
    """

    success: bool = strawberry.field(description="Whether the state change persisted")
    following: bool = strawberry.field(
        default=False, description="The new resultant state boolean flag natively"
    )
    message: str | None = strawberry.field(
        default=None, description="Detailed logging message or success"
    )
    stats: ProfileStatsType | None = strawberry.field(
        default=None, description="Updated profile stats"
    )
    error: ErrorType | None = strawberry.field(default=None, description="Explicit error object")
    error_code: str | None = strawberry.field(
        default=None, description="Detailed failure string identifier"
    )


@strawberry.type
class FollowUserType:
    """User in a followers/following list."""

    id: str
    username: str
    avatar_url: str | None = None
    is_following: bool = False


@strawberry.type
class FollowListResponse:
    """
    Paginated followers/following graph response array.

    **Authentication:** Optional depending on the query
    **Related operations:** followers, following
    """

    users: list[FollowUserType] = strawberry.field(
        description="Directly resolved mapping array of users"
    )
    next_cursor: str | None = strawberry.field(default=None, description="Passed backwards safely")
    has_more: bool = strawberry.field(
        default=False, description="Scrolling bounds limit tracking flag"
    )


@strawberry.type
class SuggestedCreatorType:
    """A suggested creator to follow."""

    id: str
    username: str
    avatar_url: str | None = None
    bio: str | None = None
    stats: ProfileStatsType | None = None


@strawberry.type
class FollowMutations:
    """Follow domain GraphQL mutations."""

    @strawberry.mutation(
        description="Optimistically toggle a direct Edge relationship connecting to a User account globally."
    )
    def follow_user(self, info: strawberry.types.Info, user_id: str) -> FollowPayload:
        """
        Create a following edge between the viewer and requested user_id.

        Is idempotent natively so repeat follow instructions succeed gracefully. Implicitly
        anchors Discovery content feeds instantly on creation natively.

        **Authentication:** Required
        **Parameters:**
        - user_id (String, required) - Valid remote Profile UUID
        **Returns:** FollowPayload tracking the new state
        **Errors:** UNAUTHENTICATED, NOT_FOUND
        """
        from core.follows.services import FollowService
        from core.shared.exceptions import FollowError

        current_user_id = _get_authenticated_user_id(info)
        if not current_user_id:
            return FollowPayload(
                success=False,
                message="Authentication required",
                error_code="UNAUTHORIZED",
            )

        try:
            result = FollowService.follow_user(current_user_id, user_id)
            from core.profiles.services import ProfileService

            profile = ProfileService.get_user_profile(user_id, current_user_id)
            stats = ProfileStatsType(
                followers_count=profile.stats.followers_count,
                following_count=profile.stats.following_count,
                posts_count=profile.stats.posts_count,
            )
            return FollowPayload(success=True, following=result.following, stats=stats)
        except FollowError as e:
            return FollowPayload(
                success=False,
                message=e.message,
                error_code=e.code,
                error=ErrorType(code=e.code, message=e.message),
            )

    @strawberry.mutation(
        description="Delete an existing following Edge relation targeting a User account."
    )
    def unfollow_user(self, info: strawberry.types.Info, user_id: str) -> FollowPayload:
        """
        Delete a following edge explicitly purging them from the Following algorithm bounds.

        Idempotent design guarantees no conflicts dynamically.

        **Authentication:** Required
        **Parameters:**
        - user_id (String, required) - Target UUID previously linked natively
        **Returns:** FollowPayload explicitly clearing the tracker (following: False)
        **Errors:** UNAUTHENTICATED
        """
        from core.follows.services import FollowService

        current_user_id = _get_authenticated_user_id(info)
        if not current_user_id:
            return FollowPayload(
                success=False,
                message="Authentication required",
                error_code="UNAUTHORIZED",
            )

        result = FollowService.unfollow_user(current_user_id, user_id)
        from core.profiles.services import ProfileService

        profile = ProfileService.get_user_profile(user_id, current_user_id)
        stats = ProfileStatsType(
            followers_count=profile.stats.followers_count,
            following_count=profile.stats.following_count,
            posts_count=profile.stats.posts_count,
        )
        return FollowPayload(success=True, following=result.following, stats=stats)


@strawberry.type
class FollowQueries:
    """Follow domain GraphQL queries."""

    @strawberry.field(
        description="Get hierarchical chronologically descending array of all User Nodes following a Profile."
    )
    def followers(
        self,
        info: strawberry.types.Info,
        user_id: str,
        cursor: str | None = None,
        limit: int = 20,
    ) -> FollowListResponse:
        """
        Retrieve paginated list of accounts pointing TO the targeted natively user.

        Appends `is_following` boolean flag on every Node resolving against the Viewer's
        State instantly determining if 'Follow Back' or identical CTA natively renders.

        **Authentication:** Optional (Viewer State defaults if unauthed)
        **Parameters:**
        - user_id (String, required) - Mapped explicit UUID natively
        - cursor (String, optional) - Pass for page bounds natively
        - limit (Int, optional) - Volume mapping
        **Returns:** FollowListResponse correctly configured array natively
        **Errors:** Return empty graph states naturally safely natively.
        """
        from core.follows.services import FollowService

        viewer_id = _get_authenticated_user_id(info)
        result = FollowService.get_followers(
            user_id=user_id,
            viewer_id=viewer_id,
            cursor=cursor,
            limit=limit,
        )

        return FollowListResponse(
            users=[
                FollowUserType(
                    id=u["user"].id,
                    username=u["user"].username,
                    avatar_url=u["user"].avatar_url,
                    is_following=u["is_following"],
                )
                for u in result["users"]
            ],
            next_cursor=result["next_cursor"],
            has_more=result["has_more"],
        )

    @strawberry.field(
        description="Get hierarchical descending list of all User Nodes a particular Profile is following."
    )
    def following(
        self,
        info: strawberry.types.Info,
        user_id: str,
        cursor: str | None = None,
        limit: int = 20,
    ) -> FollowListResponse:
        """
        Retrieve paginated list of entities the targeted account tracks actively globally.

        Yields specific context natively determining if the viewing authenticated session
        overlaps with the rendered node array via `is_following` boolean evaluation.

        **Authentication:** Optional (Viewer State returns false inherently natively)
        **Parameters:**
        - user_id (String, required) - Valid Root UUID
        - cursor (String, optional) - Hash sequence bounds
        - limit (Int, optional) - Cap array natively
        **Returns:** FollowListResponse Array structure
        **Errors:** Yields safe empty object struct bounds dynamically globally.
        """
        from core.follows.services import FollowService

        viewer_id = _get_authenticated_user_id(info)
        result = FollowService.get_following(
            user_id=user_id,
            viewer_id=viewer_id,
            cursor=cursor,
            limit=limit,
        )

        return FollowListResponse(
            users=[
                FollowUserType(
                    id=u["user"].id,
                    username=u["user"].username,
                    avatar_url=u["user"].avatar_url,
                    is_following=u["is_following"],
                )
                for u in result["users"]
            ],
            next_cursor=result["next_cursor"],
            has_more=result["has_more"],
        )

    @strawberry.field(
        description="Get highly validated creators algorithmically dynamically curated for the authenticating user."
    )
    def suggested_creators(
        self,
        info: strawberry.types.Info,
        limit: int = 10,
    ) -> list[SuggestedCreatorType]:
        """
        Pull 10 algorithmically tailored profiles optimizing the For You connections natively explicitly.

        Yields based on aggregated interest data arrays overlapping actively globally dynamically natively.

        **Authentication:** Required natively logically evaluating user graph dynamically
        **Parameters:**
        - limit (Int, optional) - Chunk Cap
        **Returns:** Directly mapped array of 10 SuggestedCreatorType nodes organically natively
        **Errors:** Falls back onto an empty list bounding cleanly globally natively gracefully without panicking natively.
        """
        from core.follows.services import FollowService

        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return []

        suggestions = FollowService.get_suggested_creators(user_id, limit)
        return [
            SuggestedCreatorType(
                id=s["user"].id,
                username=s["user"].username,
                avatar_url=s["user"].avatar_url,
                bio=s.get("bio"),
                stats=ProfileStatsType(followers_count=s.get("followers_count", 0)),
            )
            for s in suggestions
        ]

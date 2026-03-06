"""GraphQL types, queries, and mutations for the follows domain."""


import strawberry

from core.users.schema import _get_authenticated_user_id


@strawberry.type
class FollowPayload:
    """Response for follow/unfollow mutations."""

    success: bool
    following: bool = False
    message: str | None = None
    error_code: str | None = None


@strawberry.type
class FollowUserType:
    """User in a followers/following list."""

    id: str
    username: str
    avatar_url: str | None = None
    is_following: bool = False


@strawberry.type
class FollowListResponse:
    """Paginated followers/following list response."""

    users: list[FollowUserType]
    next_cursor: str | None = None
    has_more: bool = False


@strawberry.type
class SuggestedCreatorType:
    """A suggested creator to follow."""

    id: str
    username: str
    avatar_url: str | None = None
    bio: str | None = None
    followers_count: int = 0


@strawberry.type
class FollowMutations:
    """Follow domain GraphQL mutations."""

    @strawberry.mutation(description="Follow a user")
    def follow_user(self, info: strawberry.types.Info, user_id: str) -> FollowPayload:
        """Follow another user."""
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
            return FollowPayload(success=True, following=result.following)
        except FollowError as e:
            return FollowPayload(success=False, message=e.message, error_code=e.code)

    @strawberry.mutation(description="Unfollow a user")
    def unfollow_user(self, info: strawberry.types.Info, user_id: str) -> FollowPayload:
        """Unfollow a user."""
        from core.follows.services import FollowService

        current_user_id = _get_authenticated_user_id(info)
        if not current_user_id:
            return FollowPayload(
                success=False,
                message="Authentication required",
                error_code="UNAUTHORIZED",
            )

        result = FollowService.unfollow_user(current_user_id, user_id)
        return FollowPayload(success=True, following=result.following)


@strawberry.type
class FollowQueries:
    """Follow domain GraphQL queries."""

    @strawberry.field(description="Get a user's followers")
    def followers(
        self,
        info: strawberry.types.Info,
        user_id: str,
        cursor: str | None = None,
        limit: int = 20,
    ) -> FollowListResponse:
        """Get paginated followers for a user."""
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

    @strawberry.field(description="Get users that a user follows")
    def following(
        self,
        info: strawberry.types.Info,
        user_id: str,
        cursor: str | None = None,
        limit: int = 20,
    ) -> FollowListResponse:
        """Get paginated following list for a user."""
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

    @strawberry.field(description="Get suggested creators to follow")
    def suggested_creators(
        self,
        info: strawberry.types.Info,
        limit: int = 10,
    ) -> list[SuggestedCreatorType]:
        """Get interest-based creator suggestions."""
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
                followers_count=s.get("followers_count", 0),
            )
            for s in suggestions
        ]

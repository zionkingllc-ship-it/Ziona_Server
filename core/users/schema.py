import logging
from typing import Annotated

import strawberry

from core.shared.types import ErrorType
from core.users.models import User

logger = logging.getLogger("core.users")


@strawberry.type
class UserType:
    """GraphQL representation of a User."""

    id: str
    email: str
    username: str | None
    full_name: str
    bio: str
    avatar_url: str
    role: str
    is_email_verified: bool
    location: str
    created_at: str

    @classmethod
    def from_model(cls, user: User) -> "UserType":
        """Create a UserType from a User model instance."""
        return cls(
            id=str(user.id),
            email=user.email,
            username=user.username,
            full_name=user.full_name,
            bio=user.bio,
            avatar_url=user.avatar_url,
            role=user.role,
            is_email_verified=user.is_email_verified,
            location=user.location,
            created_at=user.created_at.isoformat(),
        )


@strawberry.type
class CurrentUserResponse:
    """Authenticated user's complete data including profile and stats."""

    id: strawberry.ID
    username: str
    email: str
    displayName: str | None
    isEmailVerified: bool
    hasPassword: bool
    # Exposed so the mobile app can hide like counts on the current user's
    # own posts without requiring a separate profile query.
    hideLikeCount: bool

    profile: Annotated["UserProfileType", strawberry.lazy("core.profiles.schema")]  # noqa: F821
    stats: Annotated["ProfileStatsType", strawberry.lazy("core.profiles.schema")]  # noqa: F821

    lastNameChange: str | None = None
    lastUsernameChange: str | None = None
    createdAt: str


@strawberry.type
class UsernameCheckResult:
    """Response for checkUsernameAvailability query."""

    available: bool
    suggestions: list[str] | None = None
    reason: str | None = None


@strawberry.type
class SetInterestsPayload:
    """Response for setInterests mutation."""

    success: bool
    interests: list[str] = strawberry.field(default_factory=list)
    message: str | None = None
    error_code: str | None = None
    error: ErrorType | None = strawberry.field(default=None)


@strawberry.type
class UpdateUsernamePayload:
    """Response returned when a user updates their username."""

    success: bool
    username: str | None = None
    message: str | None = None
    error_code: str | None = None
    error: ErrorType | None = strawberry.field(default=None)


def _get_authenticated_user_id(info: strawberry.types.Info) -> str | None:
    """Extract user_id from Bearer token in request."""
    request = info.context["request"]
    auth_header = request.META.get("HTTP_AUTHORIZATION", "")

    if not auth_header.startswith("Bearer "):
        return None

    try:
        from core.authentication.tokens import TokenService

        payload = TokenService.validate_access_token(auth_header[7:])
        return payload["user_id"]
    except Exception:
        logger.debug("Token validation failed in GraphQL", exc_info=True)
        return None


@strawberry.type
class UserQueries:
    """User domain GraphQL queries."""

    @strawberry.field(description="Get the currently authenticated user's complete data")
    def me(self, info: strawberry.types.Info) -> CurrentUserResponse:
        """Get the authenticated user's profile, stats, and auth settings."""
        user_id = _get_authenticated_user_id(info)
        if not user_id:
            from core.shared.exceptions import AuthenticationError

            raise AuthenticationError("Authentication required", "UNAUTHENTICATED")

        from core.authentication.services import AuthService
        from core.profiles.schema import ProfileStatsType, ProfileViewerState, UserProfileType

        data = AuthService.get_me(user_id)

        return CurrentUserResponse(
            id=data["id"],
            username=data["username"],
            email=data["email"],
            displayName=data["displayName"],
            isEmailVerified=data["isEmailVerified"],
            hasPassword=data["hasPassword"],
            hideLikeCount=data.get("hideLikeCount", False),
            profile=UserProfileType(
                id=data["id"],
                username=data["username"],
                full_name=data["displayName"],
                bio=data["profile"]["bio"],
                avatar_url=data["profile"]["avatarUrl"],
                location=data["profile"]["location"],
                stats=ProfileStatsType(
                    _followers=data["stats"]["followersCount"],
                    _following=data["stats"]["followingCount"],
                    _posts=data["stats"]["postsCount"],
                ),
                viewer_state=ProfileViewerState(
                    is_following=False,
                    is_followed_by=False,
                    is_owner=True,
                ),
                recent_posts=[],
                created_at=data["createdAt"],
            ),
            stats=ProfileStatsType(
                _followers=data["stats"]["followersCount"],
                _following=data["stats"]["followingCount"],
                _posts=data["stats"]["postsCount"],
            ),
            lastNameChange=data.get("lastNameChange"),
            lastUsernameChange=data.get("lastUsernameChange"),
            createdAt=data["createdAt"],
        )


@strawberry.type
class UserMutations:
    """User domain GraphQL mutations."""

    @strawberry.mutation(
        description="Update the authenticated user's username (rate-limited to once every 30 days)."
    )
    def update_username(
        self,
        info: strawberry.types.Info,
        username: str,
    ) -> UpdateUsernamePayload:
        """Update a user's permanent username."""
        from core.users.services import UserService, UserServiceError

        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return UpdateUsernamePayload(
                success=False,
                message="Authentication required",
                error_code="UNAUTHORIZED",
            )

        try:
            user = UserService.set_username(user_id=user_id, username=username)
            return UpdateUsernamePayload(
                success=True, username=user.username, message="Username updated successfully."
            )
        except UserServiceError as e:
            return UpdateUsernamePayload(
                success=False,
                message=e.message,
                error_code=e.code,
                error=ErrorType(code=e.code, message=e.message),
            )

    @strawberry.mutation(description="Check if a username is available")
    def check_username_availability(
        self,
        info: strawberry.types.Info,
        username: str,
    ) -> UsernameCheckResult:
        """Check username availability and return suggestions if taken."""
        try:
            from core.users.selectors import (
                check_username_availability as _check,
            )
            from core.users.selectors import (
                suggest_usernames,
            )

            result = _check(username)

            if result["available"]:
                return UsernameCheckResult(available=True)

            suggestions = suggest_usernames(username, count=4)
            return UsernameCheckResult(
                available=False,
                reason=result.get("reason", "Username not available"),
                suggestions=suggestions if suggestions else None,
            )
        except Exception:
            logger.error("check_username_availability failed", exc_info=True)
            return UsernameCheckResult(
                available=False,
                reason="An error occurred. Please try again.",
            )

    @strawberry.mutation(description="Get username suggestions based on a name")
    def suggest_usernames(
        self,
        info: strawberry.types.Info,
        base_name: str,
    ) -> list[str]:
        """Generate available username suggestions."""
        try:
            from core.users.selectors import suggest_usernames as _suggest

            return _suggest(base_name, count=4)
        except Exception:
            logger.error("suggest_usernames failed", exc_info=True)
            return []

    @strawberry.mutation(description="Set user interests for feed personalization")
    def set_interests(
        self,
        info: strawberry.types.Info,
        interests: list[str],
    ) -> SetInterestsPayload:
        """Set user interests during onboarding."""
        try:
            from core.users.models import InterestCategory, UserInterest

            user_id = _get_authenticated_user_id(info)
            if not user_id:
                return SetInterestsPayload(
                    success=False,
                    message="Authentication required",
                    error_code="UNAUTHORIZED",
                )

            valid_interests = [c.value for c in InterestCategory]
            invalid = [i for i in interests if i not in valid_interests]
            if invalid:
                return SetInterestsPayload(
                    success=False,
                    message=f"Invalid interests: {', '.join(invalid)}. "
                    f"Valid: {', '.join(valid_interests)}",
                    error_code="VALIDATION_ERROR",
                )

            # Replace all interests
            UserInterest.objects.filter(user_id=user_id).delete()
            for interest in set(interests):
                UserInterest.objects.create(user_id=user_id, interest=interest)

            logger.info("Interests set for user_id=%s count=%d", user_id, len(interests))

            return SetInterestsPayload(
                success=True,
                interests=list(set(interests)),
            )
        except Exception:
            logger.error("set_interests failed", exc_info=True)
            return SetInterestsPayload(
                success=False,
                message="An error occurred. Please try again.",
                error_code="INTERNAL_ERROR",
            )

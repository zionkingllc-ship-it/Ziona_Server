import strawberry

from core.users.models import User


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
        return None


@strawberry.type
class UserMutations:
    """User domain GraphQL mutations."""

    @strawberry.mutation(description="Check if a username is available")
    def check_username_availability(
        self,
        info: strawberry.types.Info,
        username: str,
    ) -> UsernameCheckResult:
        """Check username availability and return suggestions if taken."""
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

    @strawberry.mutation(description="Get username suggestions based on a name")
    def suggest_usernames(
        self,
        info: strawberry.types.Info,
        base_name: str,
    ) -> list[str]:
        """Generate available username suggestions."""
        from core.users.selectors import suggest_usernames as _suggest

        return _suggest(base_name, count=4)

    @strawberry.mutation(description="Set user interests for feed personalization")
    def set_interests(
        self,
        info: strawberry.types.Info,
        interests: list[str],
    ) -> SetInterestsPayload:
        """Set user interests during onboarding."""
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
                message=f"Invalid interests: {', '.join(invalid)}. Valid: {', '.join(valid_interests)}",
                error_code="VALIDATION_ERROR",
            )

        # Replace all interests
        UserInterest.objects.filter(user_id=user_id).delete()
        for interest in set(interests):
            UserInterest.objects.create(user_id=user_id, interest=interest)

        return SetInterestsPayload(
            success=True,
            interests=list(set(interests)),
        )

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
class SetUsernamePayload:
    """Response for setUsername mutation."""

    success: bool
    user: UserType | None = None
    message: str | None = None
    error_code: str | None = None


@strawberry.type
class UsernameCheckResult:
    """Response for checkUsernameAvailability query."""

    available: bool
    suggestions: list[str] | None = None
    reason: str | None = None


@strawberry.type
class SetDobPayload:
    """Response for setDateOfBirth mutation."""

    success: bool
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

    @strawberry.mutation(description="Set or update the authenticated user's username")
    def set_username(
        self,
        info: strawberry.types.Info,
        username: str,
    ) -> SetUsernamePayload:
        """Set username during onboarding or profile edit."""
        from core.users.services import UserService, UserServiceError

        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return SetUsernamePayload(
                success=False,
                message="Authentication required",
                error_code="UNAUTHORIZED",
            )

        try:
            user = UserService.set_username(user_id, username)
            return SetUsernamePayload(
                success=True,
                user=UserType.from_model(user),
            )
        except UserServiceError as e:
            return SetUsernamePayload(
                success=False,
                message=e.message,
                error_code=e.code,
            )

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

    @strawberry.mutation(description="Set the authenticated user's date of birth")
    def set_date_of_birth(
        self,
        info: strawberry.types.Info,
        date_of_birth: str,
    ) -> SetDobPayload:
        """Set DOB during onboarding. Encrypts and stores securely."""
        from core.users.services import UserService, UserServiceError

        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return SetDobPayload(
                success=False,
                message="Authentication required",
                error_code="UNAUTHORIZED",
            )

        try:
            UserService.set_date_of_birth(user_id, date_of_birth)
            return SetDobPayload(success=True)
        except UserServiceError as e:
            return SetDobPayload(
                success=False,
                message=e.message,
                error_code=e.code,
            )

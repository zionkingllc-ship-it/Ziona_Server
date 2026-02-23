"""GraphQL types, queries, and mutations for the authentication domain."""

import strawberry
from typing import Optional

from core.users.models import User
from core.users.schema import UserType


# --- Types ---

@strawberry.type
class AuthPayload:
    """Response type for authentication mutations."""

    success: bool
    user: Optional[UserType] = None
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    message: Optional[str] = None
    error_code: Optional[str] = None


# --- Queries ---

@strawberry.type
class AuthQueries:
    """Authentication domain queries."""

    @strawberry.field(description="Get the currently authenticated user")
    def me(self, info: strawberry.types.Info) -> Optional[UserType]:
        """Return the currently authenticated user."""
        request = info.context["request"]
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")

        if not auth_header.startswith("Bearer "):
            return None

        token = auth_header[7:]

        try:
            from core.authentication.tokens import TokenService

            payload = TokenService.validate_access_token(token)
            user = User.objects.get(id=payload["user_id"])
            return UserType.from_model(user)
        except Exception:
            return None

    @strawberry.field(description="Health check for the GraphQL endpoint")
    def health(self) -> str:
        """Return a simple health check response."""
        return "OK"


# --- Mutations ---

@strawberry.type
class AuthMutations:
    """Authentication domain mutations."""

    @strawberry.mutation(description="Register a new user with email and password")
    def register(
        self,
        info: strawberry.types.Info,
        email: str,
        password: str,
        full_name: str = "",
    ) -> AuthPayload:
        """Register a new user (username set later during onboarding)."""
        from core.authentication.services import AuthService, AuthenticationError

        request = info.context["request"]
        ip = request.META.get("REMOTE_ADDR", "unknown")

        try:
            result = AuthService.register(
                email=email,
                password=password,
                full_name=full_name,
                ip_address=ip,
            )
            return AuthPayload(
                success=True,
                user=UserType.from_model(result["user"]),
                access_token=result["access_token"],
                refresh_token=result["refresh_token"],
            )
        except AuthenticationError as e:
            return AuthPayload(
                success=False,
                message=e.message,
                error_code=e.code,
            )

    @strawberry.mutation(description="Login with email and password")
    def login(
        self,
        info: strawberry.types.Info,
        email: str,
        password: str,
    ) -> AuthPayload:
        """Authenticate a user."""
        from core.authentication.services import AuthService, AuthenticationError

        request = info.context["request"]

        try:
            result = AuthService.login(
                email=email,
                password=password,
                ip_address=request.META.get("REMOTE_ADDR", "unknown"),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
            )
            return AuthPayload(
                success=True,
                user=UserType.from_model(result["user"]),
                access_token=result["access_token"],
                refresh_token=result["refresh_token"],
            )
        except AuthenticationError as e:
            return AuthPayload(
                success=False,
                message=e.message,
                error_code=e.code,
            )

    @strawberry.mutation(description="Refresh access token using refresh token")
    def refresh_token(
        self,
        info: strawberry.types.Info,
        refresh_token: str,
    ) -> AuthPayload:
        """Rotate refresh token for new token pair."""
        from core.authentication.services import AuthService, AuthenticationError

        try:
            result = AuthService.refresh_tokens(refresh_token)
            return AuthPayload(
                success=True,
                access_token=result["access_token"],
                refresh_token=result["refresh_token"],
            )
        except AuthenticationError as e:
            return AuthPayload(
                success=False,
                message=e.message,
                error_code=e.code,
            )

"""GraphQL types, queries, and mutations for the authentication domain."""

import strawberry

from core.users.models import User
from core.users.schema import UserType


@strawberry.type
class AuthPayload:
    """Response type for authentication mutations."""

    success: bool
    user: UserType | None = None
    access_token: str | None = None
    refresh_token: str | None = None
    message: str | None = None
    error_code: str | None = None


@strawberry.type
class AddPasswordPayload:
    """Response for adding a password to an OAuth account."""

    success: bool
    message: str | None = None
    user: UserType | None = None
    error_code: str | None = None


@strawberry.type
class ChangePasswordPayload:
    """Response for changing password."""

    success: bool
    message: str | None = None
    signed_out_devices: int = 0
    error_code: str | None = None


@strawberry.type
class AuthQueries:
    """Authentication domain queries."""

    @strawberry.field(description="Get the currently authenticated user")
    def me(self, info: strawberry.types.Info) -> UserType | None:
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
        from core.authentication.services import AuthenticationError, AuthService

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

    @strawberry.mutation(description="Verify email address using verification token")
    def verify_email(
        self,
        info: strawberry.types.Info,
        token: str,
    ) -> AuthPayload:
        """Verify user's email."""
        from core.authentication.services import AuthenticationError, AuthService

        try:
            AuthService.verify_email(token)
            return AuthPayload(
                success=True,
                message="Email verified successfully",
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
        from core.authentication.services import AuthenticationError, AuthService

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
        from core.authentication.services import AuthenticationError, AuthService

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

    @strawberry.mutation(description="Add a password for OAuth users")
    def add_password(
        self,
        info: strawberry.types.Info,
        password: str,
    ) -> AddPasswordPayload:
        """Add a password so OAuth users can also login with email+password."""
        from core.authentication.password_service import PasswordService
        from core.authentication.validators import AuthenticationError
        from core.users.schema import _get_authenticated_user_id

        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return AddPasswordPayload(
                success=False,
                message="Authentication required",
                error_code="UNAUTHORIZED",
            )

        try:
            result = PasswordService.add_password(user_id, password)
            return AddPasswordPayload(
                success=True,
                message="Password added successfully. You can now login with email and password.",
                user=UserType.from_model(result["user"]),
            )
        except AuthenticationError as e:
            return AddPasswordPayload(
                success=False,
                message=e.message,
                error_code=e.code,
            )

    @strawberry.mutation(description="Change password (optionally sign out other devices)")
    def change_password(
        self,
        info: strawberry.types.Info,
        current_password: str,
        new_password: str,
        sign_out_other_devices: bool = False,
    ) -> ChangePasswordPayload:
        """Change password and optionally invalidate all other sessions."""
        from core.authentication.password_service import PasswordService
        from core.authentication.tokens import TokenService
        from core.authentication.validators import AuthenticationError
        from core.users.schema import _get_authenticated_user_id

        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return ChangePasswordPayload(
                success=False,
                message="Authentication required",
                error_code="UNAUTHORIZED",
            )

        current_jti = None
        if sign_out_other_devices:
            try:
                request = info.context["request"]
                auth_header = request.META.get("HTTP_AUTHORIZATION", "")
                token = auth_header[7:]
                payload = TokenService.validate_access_token(token)
                current_jti = payload.get("jti")
            except Exception:
                return ChangePasswordPayload(
                    success=False,
                    message="Invalid or expired token. Please re-login.",
                    error_code="INVALID_TOKEN",
                )

        try:
            request = info.context["request"]
            result = PasswordService.change_password(
                user_id=user_id,
                current_password=current_password,
                new_password=new_password,
                sign_out_other_devices=sign_out_other_devices,
                current_jti=current_jti,
                ip_address=request.META.get("REMOTE_ADDR", "unknown"),
            )
            return ChangePasswordPayload(
                success=True,
                message=result["message"],
                signed_out_devices=result["signed_out_devices"],
            )
        except AuthenticationError as e:
            return ChangePasswordPayload(
                success=False,
                message=e.message,
                error_code=e.code,
            )

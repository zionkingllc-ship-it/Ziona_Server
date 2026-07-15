"""Auth GraphQL mutations — session.

Split from the former core/authentication/schema.py (no contract change).
"""

import strawberry

from core.authentication.schema.types import (  # noqa: F401
    AddPasswordPayload,
    AuthPayload,
    ChangePasswordPayload,
    GoogleOAuthPayload,
    OTPPayload,
    PasswordResetRequestPayload,
    RegisterPayload,
    VerifyOTPPayload,
    _token_metadata_kwargs,
)
from core.shared.request_utils import get_client_ip
from core.users.schema import AuthenticatedUserType


@strawberry.type
class SessionMutations:
    @strawberry.mutation(
        description=(
            "Authenticate existing user with email/password. Verified users receive tokens; "
            "unverified users receive requiresVerification and a fresh OTP."
        )
    )
    def login(
        self,
        info: strawberry.types.Info,
        email: str,
        password: str,
    ) -> AuthPayload:
        """
        Authenticate existing user with email and password.

        Validates credentials and mirrors ``AuthService.login`` exactly. Verified users
        receive JWT tokens. Unverified users receive their user record, a message, and
        ``requiresVerification=true`` after a new OTP is queued.

        **Authentication:** Not required
        **Parameters:**
        - email (String, required) - User's registered email address
        - password (String, required) - User's password
        **Returns:** AuthPayload containing user data and either JWT tokens or requiresVerification
        **Example:**
        ```graphql
        mutation Login {
          login(email: "user@example.com", password: "securePassword123!") {
            user { id username email }
            accessToken
            refreshToken
          }
        }
        ```
        **Errors:** INVALID_CREDENTIALS, EMAIL_NOT_VERIFIED, ACCOUNT_SUSPENDED
        """
        from core.authentication.services import AuthenticationError, AuthService

        request = info.context.request

        try:
            result = AuthService.login(
                email=email,
                password=password,
                ip_address=get_client_ip(request),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
            )
            return AuthPayload(
                success=True,
                user=AuthenticatedUserType.from_model(result["user"]),
                access_token=result.get("access_token"),
                refresh_token=result.get("refresh_token"),
                message=result.get("message"),
                requires_verification=result.get("requires_verification", False),
                requires_account_recovery=result.get("requires_account_recovery", False),
                recovery_reason=result.get("recovery_reason"),
                recovery_token=result.get("recovery_token"),
                deletion_scheduled_for=result.get("deletion_scheduled_for"),
                **_token_metadata_kwargs(result),
            )
        except AuthenticationError as e:
            return AuthPayload(
                success=False,
                message=e.message,
                error_code=e.code,
            )

    @strawberry.mutation(description="Rotate refresh token for new token pair.")
    def refresh_token(
        self,
        info: strawberry.types.Info,
        refresh_token: str,
    ) -> AuthPayload:
        """
        Refresh an expired access token using a valid refresh token.

        For security, refresh tokens are rotated on each use. This endpoint returns a completely
        new pair of access and refresh tokens.

        **Authentication:** Not required (uses refresh_token parameter)
        **Parameters:**
        - refresh_token (String, required) - The user's active refresh token
        **Returns:** AuthPayload with a new accessToken and a new refreshToken
        **Errors:** INVALID_REFRESH_TOKEN, TOKEN_EXPIRED
        """
        from core.authentication.services import AuthenticationError, AuthService

        try:
            request = info.context.request
            result = AuthService.refresh_tokens(
                refresh_token,
                ip_address=get_client_ip(request),
            )
            return AuthPayload(
                success=True,
                access_token=result["access_token"],
                refresh_token=result["refresh_token"],
                **_token_metadata_kwargs(result),
            )
        except AuthenticationError as e:
            return AuthPayload(
                success=False,
                message=e.message,
                error_code=e.code,
            )

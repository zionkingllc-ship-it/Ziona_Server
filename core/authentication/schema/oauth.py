"""Auth GraphQL mutations — oauth.

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
class OAuthMutations:
    @strawberry.mutation(
        description="Authenticate user via Google OAuth. Creates account if new user, or logs in existing user. Sets needsUsernameSelection=true for new OAuth users."
    )
    def google_oauth(
        self,
        info: strawberry.types.Info,
        id_token: str,
    ) -> GoogleOAuthPayload:
        """
        Authenticate user via Google OAuth.

        This securely verifies the Google ID token natively, creates a new account
        if the Google ID/email doesn't exist, and natively links it. For new accounts, `needsUsernameSelection`
        is set to true dynamically.

        **Authentication:** Not required
        **Parameters:**
        - id_token (String, required) - The exact JWT string received securely from Google
        **Returns:** GoogleOAuthPayload with user status and JWTs
        **Example:**
        ```graphql
        mutation GoogleLogin {
          googleOauth(idToken: "eyJhbGciOiJSUzI1...") {
            success
            isNewUser
            user { id username email needsUsernameSelection }
            accessToken
            refreshToken
          }
        }
        ```
        **Errors:** EMAIL_REGISTERED_WITH_PASSWORD, EMAIL_REGISTERED_WITH_DIFFERENT_PROVIDER, INVALID_OAUTH_TOKEN
        """
        from core.authentication.services import AuthService
        from core.authentication.validators import AuthenticationError

        request = info.context.request
        try:
            result = AuthService.google_oauth_login(
                id_token=id_token,
                ip_address=get_client_ip(request),
            )
            return GoogleOAuthPayload(
                success=True,
                user=AuthenticatedUserType.from_model(result["user"]),
                access_token=result.get("access_token"),
                refresh_token=result.get("refresh_token"),
                is_new_user=result["is_new_user"],
                requires_account_recovery=result.get("requires_account_recovery", False),
                recovery_reason=result.get("recovery_reason"),
                recovery_token=result.get("recovery_token"),
                deletion_scheduled_for=result.get("deletion_scheduled_for"),
                **_token_metadata_kwargs(result),
            )
        except AuthenticationError as e:
            return GoogleOAuthPayload(
                success=False,
                message=e.message,
                error_code=e.code,
            )

    @strawberry.mutation(
        description="Authenticate user via Sign in with Apple. Requires a verified identityToken and nonce."
    )
    def apple_oauth(
        self,
        info: strawberry.types.Info,
        identity_token: str,
        raw_nonce: str | None = None,
        nonce: str | None = None,
        user: strawberry.scalars.JSON | None = None,
    ) -> GoogleOAuthPayload:
        """Authenticate user via Sign in with Apple."""
        from core.authentication.services import AuthService
        from core.authentication.validators import AuthenticationError

        request = info.context.request
        try:
            result = AuthService.apple_oauth_login(
                identity_token=identity_token,
                raw_nonce=raw_nonce,
                nonce=nonce,
                apple_user=user or {},
                ip_address=get_client_ip(request),
            )
            return GoogleOAuthPayload(
                success=True,
                user=AuthenticatedUserType.from_model(result["user"]),
                access_token=result.get("access_token"),
                refresh_token=result.get("refresh_token"),
                is_new_user=result["is_new_user"],
                requires_account_recovery=result.get("requires_account_recovery", False),
                recovery_reason=result.get("recovery_reason"),
                recovery_token=result.get("recovery_token"),
                deletion_scheduled_for=result.get("deletion_scheduled_for"),
                **_token_metadata_kwargs(result),
            )
        except AuthenticationError as e:
            return GoogleOAuthPayload(
                success=False,
                message=e.message,
                error_code=e.code,
            )

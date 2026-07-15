"""Auth GraphQL payload types + shared token-metadata helper.

Split from the former core/authentication/schema.py (no contract change).
"""

import strawberry

from core.shared.types import ErrorType
from core.users.schema import AuthenticatedUserType


@strawberry.type
class AuthPayload:
    """
    Authentication response containing user details and optional JWT tokens.

    Used by token-oriented auth operations such as login and refresh. Some mutations
    return user metadata without issuing tokens immediately.

    **Authentication:** Not required
    **Related operations:** login, refresh_token, verify_email, finalize_username
    """

    success: bool = strawberry.field(
        description="Whether the authentication operation was successful"
    )
    user: AuthenticatedUserType | None = strawberry.field(
        default=None, description="The authenticated user data"
    )
    access_token: str | None = strawberry.field(
        default=None, description="JWT access token (valid for 24 hours)"
    )
    refresh_token: str | None = strawberry.field(
        default=None, description="JWT refresh token (valid for 30 days)"
    )
    access_token_expires_in: int | None = strawberry.field(default=None)
    refresh_token_expires_in: int | None = strawberry.field(default=None)
    access_token_expires_at: str | None = strawberry.field(default=None)
    refresh_token_expires_at: str | None = strawberry.field(default=None)
    requires_account_recovery: bool = strawberry.field(default=False)
    recovery_reason: str | None = strawberry.field(default=None)
    recovery_token: str | None = strawberry.field(default=None)
    deletion_scheduled_for: str | None = strawberry.field(default=None)
    message: str | None = strawberry.field(default=None, description="Success or error message")
    requires_verification: bool = strawberry.field(
        default=False,
        description="Whether the user must complete email verification before tokens are issued",
    )
    error_code: str | None = strawberry.field(
        default=None,
        description="Specific error code if operation failed (e.g. INVALID_CREDENTIALS)",
    )
    error: ErrorType | None = strawberry.field(default=None)


@strawberry.type
class RegisterPayload:
    """
    Response for password registration before email verification.

    Mirrors ``AuthService.register`` by returning the created or refreshed unverified
    user, an instructional message, and a verification flag without issuing tokens.

    **Authentication:** Not required
    **Related operations:** register, verify_otp
    """

    success: bool = strawberry.field(description="Whether the registration operation succeeded")
    user: AuthenticatedUserType | None = strawberry.field(
        default=None,
        description="The registered user data in its current unverified state",
    )
    message: str | None = strawberry.field(default=None, description="Success or error message")
    requires_verification: bool = strawberry.field(
        default=False,
        description="Whether the client must verify the user's email before login tokens are issued",
    )
    error_code: str | None = strawberry.field(
        default=None,
        description="Specific error code if the operation failed (e.g. USERNAME_TAKEN)",
    )
    error: ErrorType | None = strawberry.field(default=None)


@strawberry.type
class AddPasswordPayload:
    """
    Response for adding a password to an OAuth account.

    Returned when a user who signed up with Google successfully adds a password to their account.

    **Authentication:** Required
    **Related operations:** add_password
    """

    success: bool = strawberry.field(description="Whether the password was added successfully")
    message: str | None = strawberry.field(default=None, description="Success or error message")
    user: AuthenticatedUserType | None = strawberry.field(
        default=None, description="The updated user data"
    )
    error_code: str | None = strawberry.field(
        default=None, description="Specific error code if operation failed (e.g. UNAUTHENTICATED)"
    )
    error: ErrorType | None = strawberry.field(default=None)


@strawberry.type
class ChangePasswordPayload:
    """
    Response for changing password operations.

    Provides details on how many devices were signed out if the global signout flag was used.

    **Authentication:** Required
    **Related operations:** change_password
    """

    success: bool = strawberry.field(description="Whether the password change was successful")
    message: str | None = strawberry.field(default=None, description="Success or error message")
    signed_out_devices: int = strawberry.field(
        default=0, description="Number of other device sessions terminated"
    )
    error_code: str | None = strawberry.field(
        default=None,
        description="Specific error code if operation failed (e.g. INVALID_CREDENTIALS)",
    )
    error: ErrorType | None = strawberry.field(default=None)


@strawberry.type
class OTPPayload:
    """
    Response for OTP operations.

    Contains timing information for the UI to handle countdowns safely.

    **Authentication:** Not required
    **Related operations:** send_otp
    """

    success: bool = strawberry.field(description="Whether the OTP was sent successfully")
    message: str | None = strawberry.field(default=None, description="Success or error message")
    expires_in: int | None = strawberry.field(
        default=None, description="Seconds until the code expires"
    )
    resend_after: int | None = strawberry.field(
        default=None, description="Seconds to wait before resend is allowed"
    )
    purpose: str | None = strawberry.field(
        default=None, description="The purpose string echoed back"
    )
    error_code: str | None = strawberry.field(
        default=None, description="Specific error code if operation failed"
    )
    error: ErrorType | None = strawberry.field(default=None)


@strawberry.type
class PasswordResetRequestPayload:
    """
    Response for requesting a password reset code.

    Mirrors the password-reset request flow by returning a generic success message
    without exposing whether an account exists for the email address.

    **Authentication:** Not required
    **Related operations:** reset_password
    """

    success: bool = strawberry.field(description="Whether the request was accepted")
    message: str | None = strawberry.field(default=None, description="Success or error message")
    error_code: str | None = strawberry.field(
        default=None,
        description="Specific error code if the request failed",
    )
    error: ErrorType | None = strawberry.field(default=None)


@strawberry.type
class VerifyOTPPayload:
    """
    Response for OTP verification.

    Returns standard Auth tokens for registration/login, or a reset token for password reset.

    **Authentication:** Not required
    **Related operations:** verify_otp, confirm_password_reset
    """

    success: bool = strawberry.field(description="Whether the verification was successful")
    message: str | None = strawberry.field(default=None, description="Success or error message")
    user: AuthenticatedUserType | None = strawberry.field(
        default=None, description="User data (if applicable)"
    )
    access_token: str | None = strawberry.field(
        default=None, description="JWT access token (if applicable)"
    )
    refresh_token: str | None = strawberry.field(
        default=None, description="JWT refresh token (if applicable)"
    )
    access_token_expires_in: int | None = strawberry.field(default=None)
    refresh_token_expires_in: int | None = strawberry.field(default=None)
    access_token_expires_at: str | None = strawberry.field(default=None)
    refresh_token_expires_at: str | None = strawberry.field(default=None)
    reset_token: str | None = strawberry.field(
        default=None, description="Token to use for confirming password reset"
    )
    error_code: str | None = strawberry.field(
        default=None, description="Specific error code if operation failed"
    )
    error: ErrorType | None = strawberry.field(default=None)


@strawberry.type
class GoogleOAuthPayload:
    """
    Response for Google OAuth login/registration.

    Returns standard Auth tokens and a flag indicating if this is a newly created account.

    **Authentication:** Not required
    **Related operations:** google_oauth, finalize_username
    """

    success: bool = strawberry.field(description="Whether the authentication was successful")
    user: AuthenticatedUserType | None = strawberry.field(
        default=None, description="The authenticated user data"
    )
    access_token: str | None = strawberry.field(default=None, description="JWT access token")
    refresh_token: str | None = strawberry.field(default=None, description="JWT refresh token")
    access_token_expires_in: int | None = strawberry.field(default=None)
    refresh_token_expires_in: int | None = strawberry.field(default=None)
    access_token_expires_at: str | None = strawberry.field(default=None)
    refresh_token_expires_at: str | None = strawberry.field(default=None)
    requires_account_recovery: bool = strawberry.field(default=False)
    recovery_reason: str | None = strawberry.field(default=None)
    recovery_token: str | None = strawberry.field(default=None)
    deletion_scheduled_for: str | None = strawberry.field(default=None)
    is_new_user: bool = strawberry.field(
        default=False, description="True if a new account was created"
    )
    message: str | None = strawberry.field(default=None, description="Success or error message")
    error_code: str | None = strawberry.field(default=None, description="Specific error code")
    error: ErrorType | None = strawberry.field(default=None)


def _token_metadata_kwargs(result: dict) -> dict:
    """Build GraphQL expiry fields when a result contains a complete token pair."""
    access_token = result.get("access_token")
    refresh_token = result.get("refresh_token")
    if not access_token or not refresh_token:
        return {}
    from core.authentication.tokens import TokenService

    return TokenService.get_token_expiry_metadata(access_token, refresh_token)

"""Auth GraphQL mutations — password.

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
class PasswordMutations:
    @strawberry.mutation(
        description="Add password to OAuth-only account. Allows Google sign-in users to also login with password. Only works if user doesn't already have password."
    )
    def add_password(
        self,
        info: strawberry.types.Info,
        password: str,
    ) -> AddPasswordPayload:
        """
        Add a password to an account created via OAuth (like Google).

        Allows users who initially signed up with Google OAuth to establish a standard
        email/password login method. This only works for accounts that do not already have a password set.

        **Authentication:** Required
        **Parameters:**
        - password (String, required) - The new secure password to link to the account
        **Returns:** AddPasswordPayload indicating success and updated user data
        **Errors:** UNAUTHENTICATED, PASSWORD_ALREADY_SET, WEAK_PASSWORD
        """
        from core.authentication.services import AuthService
        from core.authentication.validators import AuthenticationError
        from core.users.schema import _get_authenticated_user_id

        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return AddPasswordPayload(
                success=False,
                message="Authentication required",
                error_code="UNAUTHENTICATED",
            )

        try:
            result = AuthService.add_password(user_id=user_id, password=password)
            return AddPasswordPayload(
                success=True,
                message="Password added successfully. You can now login with email and password.",
                user=AuthenticatedUserType.from_model(result["user"]),
            )
        except AuthenticationError as e:
            return AddPasswordPayload(
                success=False,
                message=e.message,
                error_code=e.code,
            )

    @strawberry.mutation(
        description="Change password for authenticated user. Requires current password. Can optionally sign out all other devices."
    )
    def change_password(
        self,
        info: strawberry.types.Info,
        current_password: str,
        new_password: str,
        sign_out_other_devices: bool = False,
    ) -> ChangePasswordPayload:
        """
        Change password for an authenticated user.

        Validates the current password and sets a new one. Optionally allows the user
        to forcibly sign out of all other active sessions across different devices.

        **Authentication:** Required
        **Parameters:**
        - current_password (String, required) - The user's current password
        - new_password (String, required) - The new password
        - sign_out_other_devices (Boolean, optional, default: false) - Invalidate other sessions
        **Returns:** ChangePasswordPayload with success status and count of devices signed out
        **Example:**
        ```graphql
        mutation ChangePassword {
          changePassword(
            currentPassword: "oldPassword123!"
            newPassword: "newSecurePassword456!"
            signOutOtherDevices: true
          ) {
            success
            message
            signedOutDevices
          }
        }
        ```
        **Errors:** UNAUTHENTICATED, INVALID_CREDENTIALS, WEAK_PASSWORD
        """
        from core.authentication.services import AuthService
        from core.authentication.tokens import TokenInfrastructureError, TokenService
        from core.authentication.validators import AuthenticationError
        from core.users.schema import _get_authenticated_user_id

        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return ChangePasswordPayload(
                success=False,
                message="Authentication required",
                error_code="UNAUTHENTICATED",
            )

        current_jti = None
        if sign_out_other_devices:
            try:
                request = info.context.request
                auth_header = request.META.get("HTTP_AUTHORIZATION", "")
                token = auth_header[7:]
                payload = TokenService.validate_access_token(token, enforce_revocation=True)
                current_jti = payload.get("jti")
            except TokenInfrastructureError:
                return ChangePasswordPayload(
                    success=False,
                    message="Authentication service is temporarily unavailable. Please try again.",
                    error_code="AUTH_SERVICE_UNAVAILABLE",
                )
            except Exception:
                return ChangePasswordPayload(
                    success=False,
                    message="Invalid or expired token. Please re-login.",
                    error_code="INVALID_TOKEN",
                )

        try:
            request = info.context.request
            result = AuthService.change_password(
                user_id=user_id,
                current_password=current_password,
                new_password=new_password,
                sign_out_other_devices=sign_out_other_devices,
                current_jti=current_jti,
                ip_address=get_client_ip(request),
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

    @strawberry.mutation(
        description=(
            "Request a password reset code for the email/password flow. "
            "Returns a generic success message to avoid exposing account existence."
        )
    )
    def reset_password(
        self,
        info: strawberry.types.Info,
        email: str,
    ) -> PasswordResetRequestPayload:
        """
        Request password reset via email.

        This mirrors ``AuthService.request_password_reset`` and the REST
        ``/api/auth/password-reset`` endpoint. The public response is intentionally
        generic whether or not an account exists for the given email.

        **Authentication:** Not required
        **Parameters:**
        - email (String, required) - The user's secure active email mapping
        **Returns:** PasswordResetRequestPayload with a generic status message
        **Errors:** OTP_STORAGE_FAILED, OTP_EMAIL_QUEUE_FAILED
        """
        from core.authentication.services import AuthService
        from core.authentication.validators import AuthenticationError

        request = info.context.request
        try:
            AuthService.request_password_reset(
                email=email,
                ip_address=get_client_ip(request),
            )
            return PasswordResetRequestPayload(
                success=True,
                message="If an account with this email exists, a reset code has been sent.",
            )
        except AuthenticationError as e:
            return PasswordResetRequestPayload(
                success=False,
                message=e.message,
                error_code=e.code,
            )

    @strawberry.mutation(
        description=(
            "Complete password reset using the resetToken returned by verifyOtp(password_reset). "
            "Returns the authenticated user and a fresh token pair."
        )
    )
    def confirm_password_reset(
        self,
        info: strawberry.types.Info,
        reset_token: str,
        new_password: str,
        sign_out_all_devices: bool = False,
    ) -> AuthPayload:
        """
        Complete password reset using resetToken natively.

        This mirrors ``AuthService.reset_password_with_token`` exactly. It consumes the
        reset token emitted by ``verifyOtp(purpose: "password_reset")``, sets the new
        password, revokes prior sessions, and returns a fresh access/refresh token pair.

        **Authentication:** Not required
        **Parameters:**
        - reset_token (String, required) - Short-lived token verifying OTP fulfillment
        - new_password (String, required) - New password meeting password policy requirements
        - sign_out_all_devices (Boolean, optional, default: false) - Accepted for API compatibility
        **Returns:** AuthPayload containing the user and fresh JWT tokens
        **Errors:** INVALID_RESET_TOKEN, TOKEN_VALIDATION_FAILED, WEAK_PASSWORD
        """
        from core.authentication.services import AuthService
        from core.authentication.validators import AuthenticationError

        request = info.context.request

        try:
            result = AuthService.reset_password_with_token(
                reset_token=reset_token,
                new_password=new_password,
                sign_out_all_devices=sign_out_all_devices,
                ip_address=get_client_ip(request),
            )
            return AuthPayload(
                success=True,
                user=AuthenticatedUserType.from_model(result["user"]),
                access_token=result["access_token"],
                refresh_token=result["refresh_token"],
                message="Password reset successfully",
                **_token_metadata_kwargs(result),
            )
        except AuthenticationError as e:
            return AuthPayload(
                success=False,
                message=e.message,
                error_code=e.code,
            )

"""Auth GraphQL mutations — register.

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
class RegisterMutations:
    @strawberry.mutation(
        description=(
            "Create a new unverified user account with email, password, username, and date of birth. "
            "Queues an OTP and returns requiresVerification instead of tokens."
        )
    )
    def register(
        self,
        info: strawberry.types.Info,
        email: str,
        password: str,
        username: str,
        date_of_birth: str,
    ) -> RegisterPayload:
        """
        Create a new user account and trigger email verification.

        This mutation follows ``AuthService.register`` exactly: it validates the email,
        password, username, and date of birth, stores an unverified account, and queues
        an OTP email. Tokens are only issued after OTP verification.

        **Authentication:** Not required
        **Parameters:**
        - email (String, required) - Valid email address for the new account
        - password (String, required) - Secure password for the account
        - username (String, required) - Unique username selected during signup
        - date_of_birth (String, required) - Date of birth in YYYY-MM-DD format
        **Returns:** RegisterPayload with user data and requiresVerification=true
        **Example:**
        ```graphql
        mutation Register {
          register(
            email: "newuser@example.com",
            password: "securePassword123!",
            username: "newuser_95",
            dateOfBirth: "1995-08-12"
          ) {
            success
            message
            requiresVerification
            user { id username email }
          }
        }
        ```
        **Errors:** EMAIL_ALREADY_REGISTERED, USERNAME_TAKEN, WEAK_PASSWORD, INVALID_EMAIL
        """
        from core.authentication.services import AuthenticationError, AuthService

        request = info.context.request
        ip = get_client_ip(request)

        try:
            result = AuthService.register(
                email=email,
                password=password,
                username=username,
                date_of_birth=date_of_birth,
                ip_address=ip,
            )
            return RegisterPayload(
                success=True,
                user=AuthenticatedUserType.from_model(result["user"]),
                message=result["message"],
                requires_verification=result.get("requires_verification", False),
            )
        except AuthenticationError as e:
            return RegisterPayload(
                success=False,
                message=e.message,
                error_code=e.code,
            )

    @strawberry.mutation(
        description=(
            "Verify the email OTP issued by password registration or unverified login. "
            "Returns the authenticated user and JWT tokens on success."
        )
    )
    def verify_email(
        self,
        info: strawberry.types.Info,
        email: str,
        code: str,
    ) -> AuthPayload:
        """
        Verify the email OTP issued by ``AuthService.register`` or ``AuthService.login``.

        This mutation mirrors the REST ``/api/auth/verify-email`` endpoint and the
        underlying ``AuthService.verify_email_otp`` flow exactly. It validates the
        ``otp:verify`` code, marks the user as verified, and returns access and refresh
        tokens immediately.

        **Authentication:** Not required
        **Parameters:**
        - email (String, required) - Email address for the account being verified
        - code (String, required) - 6-digit OTP sent by the registration/login flow
        **Returns:** AuthPayload indicating success status and tokens
        **Errors:** INVALID_OTP, OTP_EXPIRED, EMAIL_ALREADY_VERIFIED
        """
        from core.authentication.services import AuthenticationError, AuthService

        try:
            result = AuthService.verify_email_otp(email=email, code=code)
            return AuthPayload(
                success=True,
                user=AuthenticatedUserType.from_model(result["user"]),
                access_token=result["access_token"],
                refresh_token=result["refresh_token"],
                message="Email verified successfully",
                **_token_metadata_kwargs(result),
            )
        except AuthenticationError as e:
            return AuthPayload(
                success=False,
                message=e.message,
                error_code=e.code,
            )

    @strawberry.mutation(
        description=(
            "Resend the verification OTP for an unverified password account. "
            "Returns timing metadata for resend countdown UI."
        )
    )
    def resend_verification_otp(
        self,
        info: strawberry.types.Info,
        email: str,
    ) -> OTPPayload:
        """
        Resend the verification OTP for the password register/login flow.

        This mirrors ``AuthService.resend_verification_otp`` and the REST
        ``/api/auth/resend-otp`` endpoint exactly. Use this after ``register`` or an
        unverified ``login`` response, rather than the generic ``sendOtp`` route.

        **Authentication:** Not required
        **Parameters:**
        - email (String, required) - Email address for the unverified account
        **Returns:** OTPPayload with expiresIn and resendAfter values
        **Errors:** EMAIL_ALREADY_VERIFIED, TOO_MANY_REQUESTS
        """
        from core.authentication.services import AuthenticationError, AuthService

        try:
            result = AuthService.resend_verification_otp(email=email)
            return OTPPayload(
                success=True,
                message=result["message"],
                expires_in=result["expires_in"],
                purpose="email_verification",
                resend_after=result.get("resend_after", 0),
            )
        except AuthenticationError as e:
            return OTPPayload(
                success=False,
                message=e.message,
                error_code=e.code,
            )

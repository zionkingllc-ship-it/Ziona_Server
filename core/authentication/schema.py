"""GraphQL types, queries, and mutations for the authentication domain."""

import strawberry

from core.shared.request_utils import get_client_ip
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
        default=None, description="JWT access token (valid for 15 minutes)"
    )
    refresh_token: str | None = strawberry.field(
        default=None, description="JWT refresh token (valid for 7 days)"
    )
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
    is_new_user: bool = strawberry.field(
        default=False, description="True if a new account was created"
    )
    message: str | None = strawberry.field(default=None, description="Success or error message")
    error_code: str | None = strawberry.field(default=None, description="Specific error code")
    error: ErrorType | None = strawberry.field(default=None)


@strawberry.type
class AuthQueries:
    """Authentication domain queries."""

    @strawberry.field(description="Simple health check for the GraphQL endpoint.")
    def health(self) -> str:
        """
        Return a simple health check response.

        Useful for load balancers or uptime monitoring to verify the GraphQL server is responding.

        **Authentication:** Not required
        **Parameters:** None
        **Returns:** String "OK"
        **Errors:** None
        """
        return "OK"

    @strawberry.field(
        description="Generate 4 available username suggestions based on email and optional date of birth. Returns unique, available usernames."
    )
    def suggest_usernames(
        self,
        info: strawberry.types.Info,
        email: str,
        date_of_birth: str | None = None,
        dob: str | None = None,
    ) -> list[str]:
        """
        Generate available username suggestions based on user context.

        Returns exactly 4 unique, available usernames that meet the platform's minimum guidelines.

        **Authentication:** Not required
        **Parameters:**
        - email (String, required) - The user's email address
        - date_of_birth (String, optional) - Date of birth (YYYY-MM-DD)
        - dob (String, optional) - Legacy alias for date_of_birth
        **Returns:** A list of exactly 4 available username strings
        **Errors:** INVALID_EMAIL
        """
        from core.authentication.services import AuthService

        effective_dob = date_of_birth if date_of_birth is not None else dob
        return AuthService.suggest_usernames(email, effective_dob)


@strawberry.type
class AuthMutations:
    """Authentication domain mutations."""

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
            )
        except AuthenticationError as e:
            return AuthPayload(
                success=False,
                message=e.message,
                error_code=e.code,
            )

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
                access_token=result["access_token"],
                refresh_token=result["refresh_token"],
                is_new_user=result["is_new_user"],
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
                access_token=result["access_token"],
                refresh_token=result["refresh_token"],
                is_new_user=result["is_new_user"],
            )
        except AuthenticationError as e:
            return GoogleOAuthPayload(
                success=False,
                message=e.message,
                error_code=e.code,
            )

    @strawberry.mutation(
        description=(
            "Send a purpose-scoped OTP via the unified OTP service. "
            "Use this for generic OTP flows; use register/login/verifyEmail/resendVerificationOtp "
            "for the password signup verification path."
        )
    )
    def send_otp(
        self,
        info: strawberry.types.Info,
        email: str,
        purpose: str,
    ) -> OTPPayload:
        """
        Send a one-time password (OTP) code via email.

        This is a unified router for purpose-scoped OTP flows such as password reset and
        account actions. Password signup verification has a dedicated GraphQL flow:
        ``register`` -> ``verifyEmail`` or ``resendVerificationOtp``.

        **Authentication:** Not required
        **Parameters:**
        - email (String, required) - The user's active email
        - purpose (String, required) - Which flow requested the OTP ("registration", "email_verification", "password_reset")
        **Returns:** OTPPayload with execution expiry time blocks
        **Errors:** INVALID_EMAIL, TOO_MANY_REQUESTS, INVALID_PURPOSE
        """
        from core.authentication.services import AuthService
        from core.authentication.validators import AuthenticationError

        request = info.context.request
        try:
            result = AuthService.unified_send_otp(
                email=email,
                purpose=purpose,
                ip_address=get_client_ip(request),
            )
            return OTPPayload(
                success=True,
                message=result["message"],
                expires_in=result["expires_in"],
                purpose=result["purpose"],
                resend_after=result["resend_after"],
            )
        except AuthenticationError as e:
            return OTPPayload(
                success=False,
                message=e.message,
                error_code=e.code,
            )

    @strawberry.mutation(
        description=(
            "Verify a purpose-scoped OTP through the unified OTP service. "
            "For OTPs issued by password register/login, use verifyEmail instead."
        )
    )
    def verify_otp(
        self,
        info: strawberry.types.Info,
        email: str,
        code: str,
        purpose: str,
    ) -> VerifyOTPPayload:
        """
        Verify an active purpose-scoped OTP code and execute the desired sequence block.

        This mutation is backed by ``OTPService.unified_verify_otp``. It is appropriate
        for unified OTP purposes such as ``password_reset`` and explicit
        ``email_verification`` sends through ``sendOtp``. For OTPs created by the
        password ``register`` / unverified ``login`` flow, use ``verifyEmail`` instead
        because those codes are namespaced under the legacy ``verify`` purpose.

        **Authentication:** Not required
        **Parameters:**
        - email (String, required) - The user's targeted email endpoint
        - code (String, required) - The 6-digit numerical entry value
        - purpose (String, required) - Must match the string sent exactly ("registration", "email_verification", "password_reset")
        **Returns:** VerifyOTPPayload linking standard properties dynamically
        **Errors:** INVALID_OTP, OTP_EXPIRED, USER_NOT_FOUND, MAX_ATTEMPTS_EXCEEDED
        """
        from core.authentication.services import AuthService
        from core.authentication.validators import AuthenticationError

        request = info.context.request
        try:
            result = AuthService.unified_verify_otp(
                email=email,
                code=code,
                purpose=purpose,
                ip_address=get_client_ip(request),
            )

            payload = VerifyOTPPayload(
                success=True,
                message=result.get("message"),
                reset_token=result.get("reset_token"),
            )

            if "user" in result:
                payload.user = AuthenticatedUserType.from_model(result["user"])
            if "access_token" in result:
                payload.access_token = result["access_token"]
            if "refresh_token" in result:
                payload.refresh_token = result["refresh_token"]

            return payload
        except AuthenticationError as e:
            return VerifyOTPPayload(
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
            )
        except AuthenticationError as e:
            return AuthPayload(
                success=False,
                message=e.message,
                error_code=e.code,
            )

    @strawberry.mutation(
        description="Set permanent username after Google OAuth signup. Replaces temporary username (user_XXXXXXXX) with chosen username. Validates availability."
    )
    def finalize_username(
        self,
        info: strawberry.types.Info,
        username: str,
    ) -> AuthPayload:
        """
        Set permanent username inherently after Google OAuth signup natively.

        Replaces the backend-created temporary alias (`user_XXXXXXXXXXXX`) exclusively targeting
        OAuth boundary flows by confirming internal username standards rigorously before
        flipping the `needsUsernameSelection` boolean marker permanently to `false`.

        **Authentication:** Required
        **Parameters:**
        - username (String, required) - Valid string conforming to unique boundary policies natively
        **Returns:** AuthPayload echoing the updated exact user JSON dynamically.
        **Errors:** UNAUTHENTICATED, USERNAME_TAKEN, INVALID_USERNAME_FORMAT， ALREADY_FINALIZED
        """
        from core.authentication.services import AuthService
        from core.authentication.validators import AuthenticationError
        from core.users.schema import _get_authenticated_user_id

        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return AuthPayload(
                success=False,
                message="Authentication required",
                error_code="UNAUTHENTICATED",
            )

        try:
            user = AuthService.finalize_username(
                user_id=user_id,
                username=username,
            )
            return AuthPayload(
                success=True,
                user=AuthenticatedUserType.from_model(user),
            )
        except AuthenticationError as e:
            return AuthPayload(
                success=False,
                message=e.message,
                error_code=e.code,
            )

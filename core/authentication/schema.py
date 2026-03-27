"""GraphQL types, queries, and mutations for the authentication domain."""

import strawberry

from core.shared.types import ErrorType
from core.users.models import User
from core.users.schema import UserType


@strawberry.type
class AuthPayload:
    """
    Authentication response containing user object and JWT tokens.

    Returned after successful login, registration, or token refresh operations.
    Contains the user data along with access and refresh tokens for subsequent requests.

    **Authentication:** Not required
    **Related operations:** login, register, refresh_token
    """

    success: bool = strawberry.field(
        description="Whether the authentication operation was successful"
    )
    user: UserType | None = strawberry.field(
        default=None, description="The authenticated user data"
    )
    access_token: str | None = strawberry.field(
        default=None, description="JWT access token (valid for 15 minutes)"
    )
    refresh_token: str | None = strawberry.field(
        default=None, description="JWT refresh token (valid for 7 days)"
    )
    message: str | None = strawberry.field(default=None, description="Success or error message")
    error_code: str | None = strawberry.field(
        default=None,
        description="Specific error code if operation failed (e.g. INVALID_CREDENTIALS)",
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
    user: UserType | None = strawberry.field(default=None, description="The updated user data")
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
class VerifyOTPPayload:
    """
    Response for OTP verification.

    Returns standard Auth tokens for registration/login, or a reset token for password reset.

    **Authentication:** Not required
    **Related operations:** verify_otp, confirm_password_reset
    """

    success: bool = strawberry.field(description="Whether the verification was successful")
    message: str | None = strawberry.field(default=None, description="Success or error message")
    user: UserType | None = strawberry.field(default=None, description="User data (if applicable)")
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
    user: UserType | None = strawberry.field(
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

    @strawberry.field(
        description="Get complete data for currently authenticated user. Includes profile details, follower/following counts, posts count, email verification status, and password status (for OAuth users). Cached for 5 minutes."
    )
    def me(self, info: strawberry.types.Info) -> UserType | None:
        """
        Get complete data for currently authenticated user.

        Includes profile details, follower/following counts, posts count, email verification status,
        and password status (for OAuth users). Results are cached for 5 minutes.

        **Authentication:** Required
        **Parameters:** None
        **Returns:** UserType object with full profile details and stats
        **Errors:** Returns null if unauthenticated or token is invalid
        """
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
        dob: str | None = None,
    ) -> list[str]:
        """
        Generate available username suggestions based on user context.

        Returns exactly 4 unique, available usernames that meet the platform's minimum guidelines.

        **Authentication:** Not required
        **Parameters:**
        - email (String, required) - The user's email address
        - dob (String, optional) - Date of birth (YYYY-MM-DD)
        **Returns:** A list of exactly 4 available username strings
        **Errors:** INVALID_EMAIL
        """
        from core.authentication.services import AuthService

        return AuthService.suggest_usernames(email, dob)


@strawberry.type
class AuthMutations:
    """Authentication domain mutations."""

    @strawberry.mutation(
        description="Create a new user account with email and password. Returns user object and JWT tokens for immediate login."
    )
    def register(
        self,
        info: strawberry.types.Info,
        email: str,
        password: str,
        full_name: str = "",
    ) -> AuthPayload:
        """
        Create a new user account with email and password.

        Registers a new user on the platform. The username is set temporarily and is
        finalized later during the onboarding process.

        **Authentication:** Not required
        **Parameters:**
        - email (String, required) - Valid email address for the new account
        - password (String, required) - Secure password for the account
        - full_name (String, optional) - User's full name
        **Returns:** AuthPayload with user data and immediate JWT access/refresh tokens
        **Example:**
        ```graphql
        mutation Register {
          register(
            email: "newuser@example.com",
            password: "securePassword123!",
            fullName: "John Doe"
          ) {
            user { id username email }
            accessToken
            refreshToken
          }
        }
        ```
        **Errors:** EMAIL_ALREADY_EXISTS, WEAK_PASSWORD, INVALID_EMAIL
        """
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

    @strawberry.mutation(
        description="Verify email address using verification token. Returns tokens for registration/verification or resetToken for password reset."
    )
    def verify_email(
        self,
        info: strawberry.types.Info,
        token: str,
    ) -> AuthPayload:
        """
        Verify user's email using a provided token.

        Used to finalize the email verification process after an OTP/token is sent.

        **Authentication:** Not required
        **Parameters:**
        - token (String, required) - The verification token from the email
        **Returns:** AuthPayload indicating success status and tokens
        **Errors:** INVALID_TOKEN, TOKEN_EXPIRED, USER_NOT_FOUND
        """
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

    @strawberry.mutation(
        description="Authenticate existing user with email/password. Returns user data and access/refresh tokens."
    )
    def login(
        self,
        info: strawberry.types.Info,
        email: str,
        password: str,
    ) -> AuthPayload:
        """
        Authenticate existing user with email and password.

        Validates credentials and returns JWT tokens for subsequent authenticated requests.

        **Authentication:** Not required
        **Parameters:**
        - email (String, required) - User's registered email address
        - password (String, required) - User's password
        **Returns:** AuthPayload containing user data and JWT tokens
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
        from core.authentication.oauth_service import OAuthService
        from core.authentication.validators import AuthenticationError

        request = info.context["request"]
        try:
            result = OAuthService.google_oauth_login(
                id_token=id_token,
                ip_address=request.META.get("REMOTE_ADDR", "unknown"),
            )
            return GoogleOAuthPayload(
                success=True,
                user=UserType.from_model(result["user"]),
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
        description="Send one-time password code via email. Supports three purposes: registration, email_verification, password_reset. Rate-limited to 3 requests per 10 minutes."
    )
    def send_otp(
        self,
        info: strawberry.types.Info,
        email: str,
        purpose: str,
    ) -> OTPPayload:
        """
        Send a one-time password (OTP) code via email.

        This is a unified router supporting OTPs for safe registration checkpoints,
        internal email verification flows, or dedicated password recovery mechanisms.

        **Authentication:** Not required
        **Parameters:**
        - email (String, required) - The user's active email
        - purpose (String, required) - Which flow requested the OTP ("registration", "email_verification", "password_reset")
        **Returns:** OTPPayload with execution expiry time blocks
        **Errors:** INVALID_EMAIL, TOO_MANY_REQUESTS, INVALID_PURPOSE
        """
        from core.authentication.otp_service import OTPService
        from core.authentication.validators import AuthenticationError

        request = info.context["request"]
        try:
            result = OTPService.unified_send_otp(
                email=email,
                purpose=purpose,
                ip_address=request.META.get("REMOTE_ADDR", "unknown"),
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
        description="Verify OTP code and complete action. For registration/email_verification returns tokens. For password_reset returns resetToken for next step."
    )
    def verify_otp(
        self,
        info: strawberry.types.Info,
        email: str,
        code: str,
        purpose: str,
    ) -> VerifyOTPPayload:
        """
        Verify an active OTP code and execute the desired sequence block.

        Takes the user parameters correctly validating exact hash caches. If the purpose
        is "password_reset", the return object strictly populates `resetToken` for authorization logic explicitly.
        If it represents successful auth overrides (registration / verification), standard tokens are passed back.

        **Authentication:** Not required
        **Parameters:**
        - email (String, required) - The user's targeted email endpoint
        - code (String, required) - The 6-digit numerical entry value
        - purpose (String, required) - Must match the string sent exactly ("registration", "email_verification", "password_reset")
        **Returns:** VerifyOTPPayload linking standard properties dynamically
        **Errors:** INVALID_OTP, OTP_EXPIRED, USER_NOT_FOUND, MAX_ATTEMPTS_EXCEEDED
        """
        from core.authentication.otp_service import OTPService
        from core.authentication.validators import AuthenticationError

        request = info.context["request"]
        try:
            result = OTPService.unified_verify_otp(
                email=email,
                code=code,
                purpose=purpose,
                ip_address=request.META.get("REMOTE_ADDR", "unknown"),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
            )

            payload = VerifyOTPPayload(
                success=True,
                message=result.get("message"),
                reset_token=result.get("reset_token"),
            )

            if "user" in result:
                payload.user = UserType.from_model(result["user"])
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
        description="Request password reset via email. Sends OTP code to user's email address."
    )
    def reset_password(
        self,
        info: strawberry.types.Info,
        email: str,
    ) -> OTPPayload:
        """
        Request password reset via email.

        A convenience wrapper around `sendOtp(purpose: "password_reset")`. Generates
        and securely transmits a 6-digit recovery code.

        **Authentication:** Not required
        **Parameters:**
        - email (String, required) - The user's secure active email mapping
        **Returns:** OTPPayload matching standard structural timeout blocks
        **Errors:** INVALID_EMAIL, TOO_MANY_REQUESTS
        """
        from core.authentication.password_service import PasswordService
        from core.authentication.validators import AuthenticationError

        request = info.context["request"]
        try:
            result = PasswordService.request_password_reset(
                email=email,
                ip_address=request.META.get("REMOTE_ADDR", "unknown"),
            )
            return OTPPayload(
                success=True,
                message=result["message"],
                expires_in=result["expires_in"],
                purpose="password_reset",
                resend_after=result["resend_after"],
            )
        except AuthenticationError as e:
            return OTPPayload(
                success=False,
                message=e.message,
                error_code=e.code,
            )

    @strawberry.mutation(
        description="Complete password reset using resetToken from OTP verification. Sets new password and optionally signs out all other devices."
    )
    def confirm_password_reset(
        self,
        info: strawberry.types.Info,
        reset_token: str,
        new_password: str,
        sign_out_all_devices: bool = False,
    ) -> ChangePasswordPayload:
        """
        Complete password reset using resetToken natively.

        Secures external password boundaries by taking the exclusive hashed JSONWebToken (the `resetToken`)
        output strictly from the `verifyOtp` route mapped to execute a direct model commit dynamically.

        **Authentication:** Not required
        **Parameters:**
        - reset_token (String, required) - Short-lived token verifying OTP fulfillment
        - new_password (String, required) - Safely tested password limits mapped
        - sign_out_all_devices (Boolean, optional, default: false) - Invalidate prior sessions
        **Returns:** ChangePasswordPayload highlighting exactly how many device sessions dropped natively.
        **Errors:** INVALID_TOKEN, TOKEN_EXPIRED, WEAK_PASSWORD
        """
        from core.authentication.password_service import PasswordService
        from core.authentication.validators import AuthenticationError

        try:
            result = PasswordService.confirm_password_reset(
                reset_token=reset_token,
                new_password=new_password,
                sign_out_all_devices=sign_out_all_devices,
            )
            return ChangePasswordPayload(
                success=True,
                message=result["message"],
                signed_out_devices=result.get("signed_out_devices", 0),
            )
        except AuthenticationError as e:
            return ChangePasswordPayload(
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
                error_code="UNAUTHORIZED",
            )

        try:
            user = AuthService.finalize_username(
                user_id=user_id,
                username=username,
            )
            return AuthPayload(
                success=True,
                user=UserType.from_model(user),
            )
        except AuthenticationError as e:
            return AuthPayload(
                success=False,
                message=e.message,
                error_code=e.code,
            )

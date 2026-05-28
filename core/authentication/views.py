"""
REST API views for authentication endpoints.

All endpoints return standardized JSON responses using response_helpers:
- Success: {success: true, data: {...}}
- Error: {success: false, error: {message, code, details?}}

HTTP status codes follow REST conventions:
- 200: Success
- 201: Created (new registration)
- 400: Validation / client error
- 401: Authentication error
- 429: Rate limit exceeded
"""

import json
import logging

from django.http import HttpRequest, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from core.authentication.response_helpers import (
    auth_success_response,
    build_tokens_dict,
    error_response,
    success_response,
)
from core.authentication.services import AuthenticationError, AuthService
from core.users.models import User

logger = logging.getLogger("core.authentication")


def _get_client_ip(request: HttpRequest) -> str:
    """Extract client IP from request."""
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


def _parse_json_body(request: HttpRequest) -> dict:
    """Parse JSON body from request."""
    try:
        return json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return {}


def _auth_error_response(e: AuthenticationError) -> JsonResponse:
    """Convert an AuthenticationError into a standardized error response."""
    status_map = {
        "UNAUTHENTICATED": 401,
        "INVALID_TOKEN": 401,
        "INVALID_REFRESH_TOKEN": 401,
        "MISSING_TOKEN": 401,
        "USER_NOT_FOUND": 404,
        "ACCOUNT_NOT_FOUND": 404,
        "INVALID_CREDENTIALS": 401,
        "ACCOUNT_SUSPENDED": 403,
        "ACCOUNT_DEACTIVATED": 403,
        "REAUTHENTICATION_REQUIRED": 400,
        "DELETION_ACKNOWLEDGEMENT_REQUIRED": 400,
        "PASSWORD_AUTH_UNAVAILABLE": 400,
        "APPLE_KEYS_TIMEOUT": 503,
        "APPLE_KEYS_UNAVAILABLE": 503,
        "APPLE_KEYS_INVALID": 503,
        "OAUTH_NOT_CONFIGURED": 503,
        "APPLE_TOKEN_EXPIRED": 401,
    }
    status_code = status_map.get(e.code, 400)

    return error_response(
        message=e.message,
        code=e.code,
        details=e.details if e.details else None,
        status=status_code,
    )


def _authenticated_user_id_from_request(request: HttpRequest) -> str:
    """Validate a Bearer token and return its user_id."""
    auth_header = request.META.get("HTTP_AUTHORIZATION", "")
    access_token = auth_header[7:] if auth_header.startswith("Bearer ") else ""

    if not access_token:
        raise AuthenticationError("Authentication required", "UNAUTHENTICATED")

    from core.authentication.tokens import TokenError, TokenService

    try:
        payload = TokenService.validate_access_token(access_token)
    except TokenError:
        raise AuthenticationError("Invalid or expired token", "INVALID_TOKEN") from None

    user_id = payload.get("user_id")
    if not user_id:
        raise AuthenticationError("Invalid token payload", "INVALID_TOKEN")
    return user_id


def _account_action_payload(data: dict) -> dict[str, object]:
    """Normalize account lifecycle body fields from mobile/web clients."""
    return {
        "password": data.get("password") or "",
        "otp": data.get("otp") or data.get("code") or "",
        "acknowledge_permanent_deletion": bool(
            data.get("acknowledge_permanent_deletion") or data.get("acknowledgePermanentDeletion")
        ),
    }


class BaseAuthView(View):
    """Base view for auth endpoints handling 405 Method Not Allowed and CORS OPTIONS."""

    def options(self, request: HttpRequest, *args, **kwargs) -> JsonResponse:
        """Handle CORS preflight requests."""
        allowed_methods = [m.upper() for m in self.http_method_names if hasattr(self, m.lower())]
        response = success_response()
        response["Allow"] = ", ".join(allowed_methods)
        return response

    def http_method_not_allowed(self, request: HttpRequest, *args, **kwargs) -> JsonResponse:
        """Return standardized JSON error for unsupported HTTP methods."""
        allowed_methods = [m.upper() for m in self.http_method_names if hasattr(self, m.lower())]
        return error_response(
            message=f"Method {request.method} not allowed. Use {', '.join(allowed_methods)}",
            code="METHOD_NOT_ALLOWED",
            details={"allowedMethods": allowed_methods},
            status=405,
        )


@method_decorator(csrf_exempt, name="dispatch")
class CheckEmailView(BaseAuthView):
    """Check if an email is registered endpoint.

    POST /api/auth/check-email
    Body: { email }

    Public endpoint used to determine whether to prompt for login or signup.
    Rate limited by IP.
    """

    def post(self, request: HttpRequest) -> JsonResponse:
        data = _parse_json_body(request)
        email = data.get("email", "")

        if not email:
            return error_response(
                message="Email is required",
                code="MISSING_FIELDS",
            )

        email = email.lower().strip()

        if "@" not in email:
            return error_response(
                message="Invalid email format",
                code="INVALID_EMAIL",
            )

        parts = email.split("@")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            return error_response(
                message="Invalid email format",
                code="INVALID_EMAIL",
            )

        exists = User.objects.filter(email=email, is_email_verified=True).exists()

        message = "Email already registered" if exists else "Email available"

        return success_response(
            data={
                "exists": exists,
                "message": message,
            }
        )


@method_decorator(csrf_exempt, name="dispatch")
class RegisterView(BaseAuthView):
    """User registration endpoint.

    POST /api/auth/register
    Body: { email, password, username, date_of_birth }

    Scenarios:
    - New email: creates user, sends OTP → 201
    - Existing unverified email: updates user data, sends OTP → 200
    - Existing verified email: returns EMAIL_ALREADY_REGISTERED → 400
    """

    def post(self, request: HttpRequest) -> JsonResponse:
        data = _parse_json_body(request)

        email = data.get("email", "")
        password = data.get("password", "")
        username = data.get("username", "")
        date_of_birth = data.get("date_of_birth", "")

        if not all([email, password, username, date_of_birth]):
            return error_response(
                message="Email, password, username, and date_of_birth are required",
                code="MISSING_FIELDS",
            )

        try:
            result = AuthService.register(
                email=email,
                password=password,
                username=username,
                date_of_birth=date_of_birth,
                ip_address=_get_client_ip(request),
            )

            is_new = "updated" not in result.get("message", "").lower()
            return auth_success_response(
                user=result["user"],
                message=result["message"],
                requires_verification=result.get("requires_verification", True),
                status=201 if is_new else 200,
            )
        except AuthenticationError as e:
            logger.warning("Registration failed: code=%s email=%s", e.code, email[:3])
            return _auth_error_response(e)


@method_decorator(csrf_exempt, name="dispatch")
class LoginView(BaseAuthView):
    """User login endpoint.

    POST /api/auth/login
    Body: { email, password }

    Scenarios:
    - Valid + verified: returns tokens → 200
    - Valid + unverified: sends OTP, requiresVerification → 200
    - Invalid credentials: INVALID_CREDENTIALS → 401
    """

    def post(self, request: HttpRequest) -> JsonResponse:
        data = _parse_json_body(request)

        email = data.get("email", "")
        password = data.get("password", "")

        if not email or not password:
            return error_response(
                message="Email and password are required",
                code="MISSING_FIELDS",
            )

        try:
            result = AuthService.login(
                email=email,
                password=password,
                ip_address=_get_client_ip(request),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
            )

            if result.get("requires_verification"):
                return auth_success_response(
                    user=result["user"],
                    message=result["message"],
                    requires_verification=True,
                )

            return auth_success_response(
                user=result["user"],
                access_token=result["access_token"],
                refresh_token=result["refresh_token"],
            )
        except AuthenticationError as e:
            logger.warning("Login failed: code=%s", e.code)
            return _auth_error_response(e)


@method_decorator(csrf_exempt, name="dispatch")
class TokenRefreshView(BaseAuthView):
    """Token refresh endpoint.

    POST /api/auth/refresh
    Body: { refresh_token }
    Returns: { success, data: { tokens: { accessToken, refreshToken } } }
    """

    def post(self, request: HttpRequest) -> JsonResponse:
        data = _parse_json_body(request)
        refresh_token = data.get("refresh_token", "")

        if not refresh_token:
            return error_response(
                message="Refresh token is required",
                code="MISSING_FIELDS",
            )

        try:
            result = AuthService.refresh_tokens(refresh_token)
            return success_response(
                data={
                    "tokens": build_tokens_dict(
                        result["access_token"],
                        result["refresh_token"],
                    ),
                },
            )
        except AuthenticationError as e:
            logger.warning("Token refresh failed: code=%s", e.code)
            return _auth_error_response(e)


@method_decorator(csrf_exempt, name="dispatch")
class LogoutView(BaseAuthView):
    """User logout endpoint.

    POST /api/auth/logout
    Headers: Authorization: Bearer <access_token>
    Body: { refresh_token? }
    """

    def post(self, request: HttpRequest) -> JsonResponse:
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        access_token = ""
        if auth_header.startswith("Bearer "):
            access_token = auth_header[7:]

        data = _parse_json_body(request)
        refresh_token = data.get("refresh_token")

        if not access_token:
            return error_response(
                message="Authorization header with Bearer token is required",
                code="MISSING_TOKEN",
                status=401,
            )

        AuthService.logout(
            access_token=access_token,
            refresh_token=refresh_token,
        )

        logger.info("User logged out")
        return success_response(data={"message": "Logged out successfully"})


@method_decorator(csrf_exempt, name="dispatch")
class VerifyEmailView(BaseAuthView):
    """Email verification via OTP endpoint.

    POST /api/auth/verify-email
    Body: { email, code }
    Returns tokens on success in standardized format.
    """

    def post(self, request: HttpRequest) -> JsonResponse:
        data = _parse_json_body(request)
        email = data.get("email", "")
        code = data.get("code", "")

        if not email or not code:
            return error_response(
                message="Email and verification code are required",
                code="MISSING_FIELDS",
            )

        try:
            result = AuthService.verify_email_otp(email=email, code=code)
            return auth_success_response(
                user=result["user"],
                access_token=result["access_token"],
                refresh_token=result["refresh_token"],
            )
        except AuthenticationError as e:
            logger.warning("Email verification failed: code=%s", e.code)
            return _auth_error_response(e)


@method_decorator(csrf_exempt, name="dispatch")
class ResendOTPView(BaseAuthView):
    """Resend email verification OTP endpoint.

    POST /api/auth/resend-otp
    Body: { email }
    Rate limited: 3 per 10 minutes per email.
    """

    def post(self, request: HttpRequest) -> JsonResponse:
        data = _parse_json_body(request)
        email = data.get("email", "")

        if not email:
            return error_response(
                message="Email is required",
                code="MISSING_FIELDS",
            )

        try:
            result = AuthService.resend_verification_otp(email=email)
            return success_response(
                data={
                    "message": result["message"],
                    "expiresIn": result["expires_in"],
                },
            )
        except AuthenticationError as e:
            logger.warning("Resend OTP failed: code=%s", e.code)
            return _auth_error_response(e)


@method_decorator(csrf_exempt, name="dispatch")
class SuggestUsernamesView(BaseAuthView):
    """Username suggestions endpoint.

    POST /api/auth/suggest-usernames
    Body: { email, date_of_birth }
    """

    def post(self, request: HttpRequest) -> JsonResponse:
        data = _parse_json_body(request)
        email = data.get("email", "")
        date_of_birth = data.get("date_of_birth")

        if not email:
            return error_response(
                message="Email is required",
                code="MISSING_FIELDS",
            )

        try:
            suggestions = AuthService.suggest_usernames(
                email=email,
                date_of_birth=date_of_birth,
            )
            return success_response(data={"suggestions": suggestions})
        except AuthenticationError as e:
            return _auth_error_response(e)


@method_decorator(csrf_exempt, name="dispatch")
class PasswordResetRequestView(BaseAuthView):
    """Password reset request endpoint.

    POST /api/auth/password-reset
    Body: { email }
    Always returns success to prevent email enumeration.
    """

    def post(self, request: HttpRequest) -> JsonResponse:
        data = _parse_json_body(request)
        email = data.get("email", "")

        if not email:
            return error_response(
                message="Email is required",
                code="MISSING_FIELDS",
            )

        try:
            AuthService.request_password_reset(
                email=email,
                ip_address=_get_client_ip(request),
            )
        except Exception:
            logger.error("Password reset request failed", exc_info=True)

        return success_response(
            data={
                "message": "If an account with this email exists, a reset code has been sent.",
            },
        )


@method_decorator(csrf_exempt, name="dispatch")
class PasswordResetConfirmView(BaseAuthView):
    """Password reset confirmation endpoint.

    POST /api/auth/password-reset/confirm
    Body: { email, otp, new_password }
    """

    def post(self, request: HttpRequest) -> JsonResponse:
        data = _parse_json_body(request)

        email = data.get("email", "")
        otp = data.get("otp", "")
        new_password = data.get("new_password", "")

        if not all([email, otp, new_password]):
            return error_response(
                message="Email, OTP, and new password are required",
                code="MISSING_FIELDS",
            )

        try:
            AuthService.reset_password(
                email=email,
                otp=otp,
                new_password=new_password,
                ip_address=_get_client_ip(request),
            )
            return success_response(
                data={"message": "Password reset successfully"},
            )
        except AuthenticationError as e:
            logger.warning("Password reset failed: code=%s", e.code)
            return _auth_error_response(e)


@method_decorator(csrf_exempt, name="dispatch")
class GoogleOAuthView(BaseAuthView):
    """Google OAuth endpoint.

    POST /api/auth/google
    Body: { id_token }
    Returns tokens and user info with isNewUser flag.
    """

    def post(self, request: HttpRequest) -> JsonResponse:
        data = _parse_json_body(request)
        id_token = data.get("id_token", "")

        if not id_token:
            return error_response(
                message="Google ID token is required",
                code="MISSING_FIELDS",
            )

        try:
            result = AuthService.google_oauth_login(
                id_token=id_token,
                ip_address=_get_client_ip(request),
            )

            response_data = {
                "user": {
                    "id": str(result["user"].id),
                    "email": result["user"].email,
                    "username": result["user"].username,
                    "role": result["user"].role,
                    "isEmailVerified": result["user"].is_email_verified,
                    "needsUsernameSelection": getattr(
                        result["user"], "needs_username_selection", False
                    ),
                },
                "tokens": build_tokens_dict(
                    result["access_token"],
                    result["refresh_token"],
                ),
                "isNewUser": result["is_new_user"],
            }
            return success_response(data=response_data)
        except AuthenticationError as e:
            logger.warning("Google OAuth failed: code=%s", e.code)
            return _auth_error_response(e)


@method_decorator(csrf_exempt, name="dispatch")
class AppleNonceView(BaseAuthView):
    """Issue a short-lived nonce challenge for Sign in with Apple.

    POST /api/auth/apple/nonce
    Returns: { rawNonce, nonce, expiresIn }
    """

    def post(self, request: HttpRequest) -> JsonResponse:
        from core.authentication.apple_oauth import create_apple_nonce

        return success_response(data=create_apple_nonce())


@method_decorator(csrf_exempt, name="dispatch")
class AppleOAuthView(BaseAuthView):
    """Sign in with Apple endpoint.

    POST /api/auth/apple
    Body: { identityToken, rawNonce, nonce?, user? }
    Returns tokens and user info with isNewUser flag.
    """

    def post(self, request: HttpRequest) -> JsonResponse:
        data = _parse_json_body(request)
        identity_token = data.get("identity_token") or data.get("identityToken") or ""
        raw_nonce = data.get("raw_nonce") or data.get("rawNonce")
        nonce = data.get("nonce")
        apple_user = data.get("user") or {}

        if not identity_token:
            return error_response(
                message="Apple identity token is required",
                code="MISSING_FIELDS",
            )

        try:
            result = AuthService.apple_oauth_login(
                identity_token=identity_token,
                nonce=nonce,
                raw_nonce=raw_nonce,
                apple_user=apple_user,
                ip_address=_get_client_ip(request),
            )

            response_data = {
                "user": {
                    "id": str(result["user"].id),
                    "email": result["user"].email,
                    "username": result["user"].username,
                    "role": result["user"].role,
                    "isEmailVerified": result["user"].is_email_verified,
                    "needsUsernameSelection": getattr(
                        result["user"], "needs_username_selection", False
                    ),
                },
                "tokens": build_tokens_dict(
                    result["access_token"],
                    result["refresh_token"],
                ),
                "isNewUser": result["is_new_user"],
            }
            return success_response(data=response_data)
        except AuthenticationError as e:
            logger.warning("Apple OAuth failed: code=%s", e.code)
            return _auth_error_response(e)


@method_decorator(csrf_exempt, name="dispatch")
class MeView(BaseAuthView):
    """Authenticated user info and protected account deletion.

    GET /api/auth/me
    DELETE /api/auth/me
    Headers: Authorization: Bearer <token>
    """

    def get(self, request: HttpRequest) -> JsonResponse:
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        access_token = ""
        if auth_header.startswith("Bearer "):
            access_token = auth_header[7:]

        if not access_token:
            return error_response(
                message="Authentication required",
                code="UNAUTHENTICATED",
                status=401,
            )

        try:
            from core.authentication.services import AuthService
            from core.authentication.tokens import TokenError, TokenService

            try:
                payload = TokenService.validate_access_token(access_token)
            except TokenError:
                raise AuthenticationError("Invalid or expired token", "INVALID_TOKEN") from None

            user_id = payload.get("user_id")

            if not user_id:
                raise AuthenticationError("Invalid token payload", "INVALID_TOKEN")

            data = AuthService.get_me(user_id=user_id)
            return success_response(data=data)

        except AuthenticationError as e:
            logger.warning("Get current user failed: code=%s", e.code)
            return _auth_error_response(e)

    def delete(self, request: HttpRequest) -> JsonResponse:
        try:
            user_id = _authenticated_user_id_from_request(request)
            payload = _account_action_payload(_parse_json_body(request))
            AuthService.delete_account(
                user_id=user_id,
                password=payload["password"],
                otp=payload["otp"],
                acknowledge_permanent_deletion=bool(payload["acknowledge_permanent_deletion"]),
                ip_address=_get_client_ip(request),
            )

            return success_response(
                data={"message": "Account permanently deleted."},
            )
        except AuthenticationError as e:
            logger.warning("Account deletion failed: code=%s", e.code)
            return _auth_error_response(e)


@method_decorator(csrf_exempt, name="dispatch")
class DeactivateAccountView(BaseAuthView):
    """Deactivate the authenticated user's account from Profile/Settings.

    POST /api/auth/deactivate
    Body: { password } or { otp/code }
    Headers: Authorization: Bearer <token>
    """

    def post(self, request: HttpRequest) -> JsonResponse:
        try:
            user_id = _authenticated_user_id_from_request(request)
            payload = _account_action_payload(_parse_json_body(request))
            AuthService.deactivate_account(
                user_id=user_id,
                password=payload["password"],
                otp=payload["otp"],
                ip_address=_get_client_ip(request),
            )
            return success_response(data={"message": "Account deactivated successfully."})
        except AuthenticationError as e:
            logger.warning("Account deactivation failed: code=%s", e.code)
            return _auth_error_response(e)


@method_decorator(csrf_exempt, name="dispatch")
class DeleteAccountView(BaseAuthView):
    """Protected account deletion endpoint for Profile/Settings.

    POST /api/auth/delete-account
    Body: { password/otp, acknowledgePermanentDeletion: true }
    Headers: Authorization: Bearer <token>
    """

    def post(self, request: HttpRequest) -> JsonResponse:
        try:
            user_id = _authenticated_user_id_from_request(request)
            payload = _account_action_payload(_parse_json_body(request))
            AuthService.delete_account(
                user_id=user_id,
                password=payload["password"],
                otp=payload["otp"],
                acknowledge_permanent_deletion=bool(payload["acknowledge_permanent_deletion"]),
                ip_address=_get_client_ip(request),
            )
            return success_response(data={"message": "Account permanently deleted."})
        except AuthenticationError as e:
            logger.warning("Account deletion failed: code=%s", e.code)
            return _auth_error_response(e)


@method_decorator(csrf_exempt, name="dispatch")
class FinalizeUsernameView(BaseAuthView):
    """Finalize OAuth username.

    POST /api/auth/finalize-username
    Body: { username: "new_username" }
    Headers: Authorization: Bearer <token>
    """

    def post(self, request: HttpRequest) -> JsonResponse:
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        access_token = ""
        if auth_header.startswith("Bearer "):
            access_token = auth_header[7:]

        if not access_token:
            return error_response(
                message="Authentication required",
                code="UNAUTHENTICATED",
                status=401,
            )

        try:
            from core.authentication.account_status import ensure_account_can_authenticate
            from core.authentication.tokens import TokenService
            from core.users.models import User

            payload = TokenService.validate_access_token(access_token)
            user_id = payload.get("user_id")

            if not user_id:
                raise AuthenticationError("Invalid token payload", "INVALID_TOKEN")

            user = User.objects.get(id=user_id)
            ensure_account_can_authenticate(user)

            data = _parse_json_body(request)
            username = data.get("username", "").strip()

            if not username:
                return error_response(
                    message="Username is required",
                    code="MISSING_FIELDS",
                )

            from core.authentication.validators import validate_username

            try:
                validate_username(username)
            except AuthenticationError as e:
                return error_response(
                    message=e.message,
                    code=e.code,
                )

            if User.all_objects.filter(username=username).exclude(id=user.id).exists():
                return error_response(
                    message="Username already exists",
                    code="USERNAME_TAKEN",
                )

            user.username = username
            user.needs_username_selection = False
            user.save(update_fields=["username", "needs_username_selection", "updated_at"])

            from django.core.cache import cache

            cache_key = f"user_me_data_{user.id}"
            cache.delete(cache_key)

            return success_response(
                data={
                    "username": username,
                    "message": "Username updated successfully",
                }
            )

        except AuthenticationError as e:
            return _auth_error_response(e)
        except User.DoesNotExist:
            return error_response(
                message="Authentication required",
                code="UNAUTHENTICATED",
                status=401,
            )

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
    return error_response(
        message=e.message,
        code=e.code,
        details=e.details if e.details else None,
    )


@method_decorator(csrf_exempt, name="dispatch")
class RegisterView(View):
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
class LoginView(View):
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
class TokenRefreshView(View):
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
class LogoutView(View):
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
class VerifyEmailView(View):
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
class ResendOTPView(View):
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
class SuggestUsernamesView(View):
    """Username suggestions endpoint.

    POST /api/auth/suggest-usernames
    Body: { email, date_of_birth }
    """

    def post(self, request: HttpRequest) -> JsonResponse:
        data = _parse_json_body(request)
        email = data.get("email", "")
        date_of_birth = data.get("date_of_birth", "")

        if not email or not date_of_birth:
            return error_response(
                message="Email and date_of_birth are required",
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
class PasswordResetRequestView(View):
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
class PasswordResetConfirmView(View):
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
class GoogleOAuthView(View):
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
class DeleteAccountView(View):
    """Delete authenticated user account permanently.

    DELETE /api/auth/me
    Headers: Authorization: Bearer <token>
    """

    def delete(self, request: HttpRequest) -> JsonResponse:
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        access_token = ""
        if auth_header.startswith("Bearer "):
            access_token = auth_header[7:]

        if not access_token:
            return error_response(
                message="Authorization header with Bearer token is required",
                code="MISSING_TOKEN",
                status=401,
            )

        try:
            from core.authentication.tokens import TokenService

            payload = TokenService.validate_access_token(access_token)
            user_id = payload.get("user_id")

            if not user_id:
                raise AuthenticationError("Invalid token payload", "INVALID_TOKEN")

            # Service performs hard delete
            AuthService.delete_account(user_id=user_id)

            return success_response(
                data={"message": "Account permanently deleted."},
            )
        except AuthenticationError as e:
            logger.warning("Account deletion failed: code=%s", e.code)
            return _auth_error_response(e)

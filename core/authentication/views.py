"""
REST API views for authentication endpoints.

These endpoints use standard Django views (not GraphQL) for auth operations
that benefit from REST semantics (login, register, token refresh).
"""

import json
import logging

from django.http import HttpRequest, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

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


@method_decorator(csrf_exempt, name="dispatch")
class RegisterView(View):
    """User registration endpoint.

    POST /api/auth/register
    Body: { email, password, full_name? }
    Returns: { success, data: { user, access_token, refresh_token } }
    """

    def post(self, request: HttpRequest) -> JsonResponse:
        """Handle user registration."""
        data = _parse_json_body(request)

        email = data.get("email", "")
        password = data.get("password", "")
        full_name = data.get("full_name", "")

        if not email or not password:
            return JsonResponse(
                {
                    "success": False,
                    "error": {
                        "code": "MISSING_FIELDS",
                        "message": "Email and password are required",
                    },
                },
                status=400,
            )

        try:
            result = AuthService.register(
                email=email,
                password=password,
                full_name=full_name,
                ip_address=_get_client_ip(request),
            )
            return JsonResponse(
                {
                    "success": True,
                    "data": {
                        "user": {
                            "id": str(result["user"].id),
                            "email": result["user"].email,
                            "username": result["user"].username,
                            "role": result["user"].role,
                        },
                        "access_token": result["access_token"],
                        "refresh_token": result["refresh_token"],
                    },
                },
                status=201,
            )
        except AuthenticationError as e:
            return JsonResponse(
                {"success": False, "error": {"code": e.code, "message": e.message}},
                status=400,
            )


@method_decorator(csrf_exempt, name="dispatch")
class LoginView(View):
    """User login endpoint.

    POST /api/auth/login
    Body: { email, password }
    Returns: { success, data: { user, access_token, refresh_token } }
    """

    def post(self, request: HttpRequest) -> JsonResponse:
        """Handle user login."""
        data = _parse_json_body(request)

        email = data.get("email", "")
        password = data.get("password", "")

        if not email or not password:
            return JsonResponse(
                {
                    "success": False,
                    "error": {
                        "code": "MISSING_FIELDS",
                        "message": "Email and password are required",
                    },
                },
                status=400,
            )

        try:
            result = AuthService.login(
                email=email,
                password=password,
                ip_address=_get_client_ip(request),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
            )
            return JsonResponse(
                {
                    "success": True,
                    "data": {
                        "user": {
                            "id": str(result["user"].id),
                            "email": result["user"].email,
                            "username": result["user"].username,
                            "role": result["user"].role,
                            "is_email_verified": result["user"].is_email_verified,
                        },
                        "access_token": result["access_token"],
                        "refresh_token": result["refresh_token"],
                    },
                },
                status=200,
            )
        except AuthenticationError as e:
            status = 401 if e.code == "INVALID_CREDENTIALS" else 400
            return JsonResponse(
                {"success": False, "error": {"code": e.code, "message": e.message}},
                status=status,
            )


@method_decorator(csrf_exempt, name="dispatch")
class TokenRefreshView(View):
    """Token refresh endpoint.

    POST /api/auth/refresh
    Body: { refresh_token }
    Returns: { success, data: { access_token, refresh_token } }
    """

    def post(self, request: HttpRequest) -> JsonResponse:
        """Handle token refresh."""
        data = _parse_json_body(request)
        refresh_token = data.get("refresh_token", "")

        if not refresh_token:
            return JsonResponse(
                {
                    "success": False,
                    "error": {
                        "code": "MISSING_FIELDS",
                        "message": "Refresh token is required",
                    },
                },
                status=400,
            )

        try:
            result = AuthService.refresh_tokens(refresh_token)
            return JsonResponse(
                {
                    "success": True,
                    "data": {
                        "access_token": result["access_token"],
                        "refresh_token": result["refresh_token"],
                    },
                },
                status=200,
            )
        except AuthenticationError as e:
            return JsonResponse(
                {"success": False, "error": {"code": e.code, "message": e.message}},
                status=401,
            )


@method_decorator(csrf_exempt, name="dispatch")
class LogoutView(View):
    """User logout endpoint.

    POST /api/auth/logout
    Headers: Authorization: Bearer <access_token>
    Body: { refresh_token? }
    Returns: { success }
    """

    def post(self, request: HttpRequest) -> JsonResponse:
        """Handle user logout."""
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        access_token = ""
        if auth_header.startswith("Bearer "):
            access_token = auth_header[7:]

        data = _parse_json_body(request)
        refresh_token = data.get("refresh_token")

        if not access_token:
            return JsonResponse(
                {
                    "success": False,
                    "error": {
                        "code": "MISSING_TOKEN",
                        "message": "Authorization header with Bearer token is required",
                    },
                },
                status=401,
            )

        AuthService.logout(
            access_token=access_token,
            refresh_token=refresh_token,
        )

        return JsonResponse({"success": True, "message": "Logged out successfully"})


@method_decorator(csrf_exempt, name="dispatch")
class VerifyEmailView(View):
    """Email verification endpoint.

    POST /api/auth/verify-email
    Body: { token }
    Returns: { success }
    """

    def post(self, request: HttpRequest) -> JsonResponse:
        """Handle email verification."""
        data = _parse_json_body(request)
        token = data.get("token", "")

        if not token:
            return JsonResponse(
                {
                    "success": False,
                    "error": {
                        "code": "MISSING_FIELDS",
                        "message": "Verification token is required",
                    },
                },
                status=400,
            )

        try:
            AuthService.verify_email(token)
            return JsonResponse({"success": True, "message": "Email verified successfully"})
        except AuthenticationError as e:
            return JsonResponse(
                {"success": False, "error": {"code": e.code, "message": e.message}},
                status=400,
            )


@method_decorator(csrf_exempt, name="dispatch")
class PasswordResetRequestView(View):
    """Password reset request endpoint.

    POST /api/auth/password-reset
    Body: { email }
    Returns: { success } (always true to prevent enumeration)
    """

    def post(self, request: HttpRequest) -> JsonResponse:
        """Handle password reset request."""
        data = _parse_json_body(request)
        email = data.get("email", "")

        if not email:
            return JsonResponse(
                {
                    "success": False,
                    "error": {
                        "code": "MISSING_FIELDS",
                        "message": "Email is required",
                    },
                },
                status=400,
            )

        AuthService.request_password_reset(
            email=email,
            ip_address=_get_client_ip(request),
        )

        return JsonResponse(
            {
                "success": True,
                "message": "If an account with this email exists, a reset code has been sent.",
            }
        )


@method_decorator(csrf_exempt, name="dispatch")
class PasswordResetConfirmView(View):
    """Password reset confirmation endpoint.

    POST /api/auth/password-reset/confirm
    Body: { email, otp, new_password }
    Returns: { success }
    """

    def post(self, request: HttpRequest) -> JsonResponse:
        """Handle password reset confirmation."""
        data = _parse_json_body(request)

        email = data.get("email", "")
        otp = data.get("otp", "")
        new_password = data.get("new_password", "")

        if not email or not otp or not new_password:
            return JsonResponse(
                {
                    "success": False,
                    "error": {
                        "code": "MISSING_FIELDS",
                        "message": "Email, OTP, and new password are required",
                    },
                },
                status=400,
            )

        try:
            AuthService.reset_password(
                email=email,
                otp=otp,
                new_password=new_password,
                ip_address=_get_client_ip(request),
            )
            return JsonResponse({"success": True, "message": "Password reset successfully"})
        except AuthenticationError as e:
            return JsonResponse(
                {"success": False, "error": {"code": e.code, "message": e.message}},
                status=400,
            )


@method_decorator(csrf_exempt, name="dispatch")
class GoogleOAuthView(View):
    """Google OAuth endpoint.

    POST /api/auth/google
    Body: { id_token }
    Returns: { success, data: { user, access_token, refresh_token, is_new_user } }
    """

    def post(self, request: HttpRequest) -> JsonResponse:
        """Handle Google OAuth login."""
        data = _parse_json_body(request)
        id_token = data.get("id_token", "")

        if not id_token:
            return JsonResponse(
                {
                    "success": False,
                    "error": {
                        "code": "MISSING_FIELDS",
                        "message": "Google ID token is required",
                    },
                },
                status=400,
            )

        try:
            result = AuthService.google_oauth_login(
                id_token=id_token,
                ip_address=_get_client_ip(request),
            )
            return JsonResponse(
                {
                    "success": True,
                    "data": {
                        "user": {
                            "id": str(result["user"].id),
                            "email": result["user"].email,
                            "username": result["user"].username,
                            "role": result["user"].role,
                        },
                        "access_token": result["access_token"],
                        "refresh_token": result["refresh_token"],
                        "is_new_user": result["is_new_user"],
                    },
                },
                status=200,
            )
        except AuthenticationError as e:
            return JsonResponse(
                {"success": False, "error": {"code": e.code, "message": e.message}},
                status=401,
            )

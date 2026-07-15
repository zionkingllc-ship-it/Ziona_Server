"""Account creation + session views (check-email, register, login, refresh, logout).

Split from the former core/authentication/views.py (no behavior change).
"""

import logging

from django.http import HttpRequest, JsonResponse
from django.utils.decorators import method_decorator
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


from core.authentication.views.base import (  # noqa: E402,F401
    BaseAuthView,
    _auth_error_response,
    _authenticated_user_id_from_request,
    _get_client_ip,
    _parse_json_body,
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

            if result.get("requires_account_recovery"):
                return auth_success_response(
                    user=result["user"],
                    requires_account_recovery=True,
                    recovery_reason=result["recovery_reason"],
                    recovery_token=result["recovery_token"],
                    deletion_scheduled_for=result.get("deletion_scheduled_for"),
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
            result = AuthService.refresh_tokens(refresh_token, ip_address=_get_client_ip(request))
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

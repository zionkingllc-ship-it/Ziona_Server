"""Password + email-verification views.

Split from the former core/authentication/views.py (no behavior change).
"""

import logging

from django.http import HttpRequest, JsonResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt

from core.authentication.response_helpers import (
    auth_success_response,
    error_response,
    success_response,
)
from core.authentication.services import AuthenticationError, AuthService

logger = logging.getLogger("core.authentication")


from core.authentication.views.base import (  # noqa: E402,F401
    BaseAuthView,
    _auth_error_response,
    _authenticated_user_id_from_request,
    _get_client_ip,
    _parse_json_body,
)


@method_decorator(csrf_exempt, name="dispatch")
class ChangePasswordView(BaseAuthView):
    """Authenticated password change endpoint.

    POST /api/auth/change-password
    Headers: Authorization: Bearer <access_token>
    Body: { currentPassword, newPassword, signOutOtherDevices? }
    """

    def post(self, request: HttpRequest) -> JsonResponse:
        try:
            user_id = _authenticated_user_id_from_request(request)
        except AuthenticationError as e:
            return _auth_error_response(e)

        data = _parse_json_body(request)
        current_password = data.get("currentPassword") or data.get("current_password") or ""
        new_password = data.get("newPassword") or data.get("new_password") or ""
        sign_out_other_devices = bool(
            data.get("signOutOtherDevices") or data.get("sign_out_other_devices")
        )

        if not current_password or not new_password:
            return error_response(
                message="Current password and new password are required",
                code="MISSING_FIELDS",
            )

        current_jti = None
        if sign_out_other_devices:
            from core.authentication.tokens import (
                TokenError,
                TokenInfrastructureError,
                TokenService,
            )

            auth_header = request.META.get("HTTP_AUTHORIZATION", "")
            access_token = auth_header[7:] if auth_header.startswith("Bearer ") else ""
            try:
                payload = TokenService.validate_access_token(
                    access_token,
                    enforce_revocation=True,
                )
            except TokenInfrastructureError:
                return error_response(
                    message="Authentication service is temporarily unavailable. Please try again.",
                    code="AUTH_SERVICE_UNAVAILABLE",
                    status=503,
                )
            except TokenError:
                return error_response(
                    message="Invalid or expired token. Please re-login.",
                    code="INVALID_TOKEN",
                    status=401,
                )

            current_jti = payload.get("jti")
            if not current_jti:
                return error_response(
                    message="Invalid token payload. Please re-login.",
                    code="INVALID_TOKEN",
                    status=401,
                )

        try:
            result = AuthService.change_password(
                user_id=user_id,
                current_password=current_password,
                new_password=new_password,
                sign_out_other_devices=sign_out_other_devices,
                current_jti=current_jti,
                ip_address=_get_client_ip(request),
            )
            return success_response(
                data={
                    "message": result["message"],
                    "signedOutDevices": result["signed_out_devices"],
                }
            )
        except AuthenticationError as e:
            logger.warning("Password change failed: code=%s", e.code)
            return _auth_error_response(e)


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

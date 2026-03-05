"""
Unified OTP views — single endpoints for sending and verifying OTPs.

POST /api/auth/otp/send   → { email, purpose }
POST /api/auth/otp/verify  → { email, code, purpose }
"""

import json
import logging

from django.http import HttpRequest, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from core.authentication.otp_service import OTPService
from core.authentication.response_helpers import (
    build_tokens_dict,
    build_user_dict,
    error_response,
    success_response,
)
from core.authentication.validators import AuthenticationError

logger = logging.getLogger("core.authentication")


def _parse_json_body(request: HttpRequest) -> dict:
    """Parse JSON body from request."""
    try:
        return json.loads(request.body) if request.body else {}
    except (json.JSONDecodeError, ValueError):
        return {}


def _get_client_ip(request: HttpRequest) -> str:
    """Extract client IP from request."""
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def _auth_error_response(e: AuthenticationError) -> JsonResponse:
    """Convert AuthenticationError to standardized error response."""
    return error_response(
        message=e.message,
        code=e.code,
        details=e.details if e.details else None,
    )


@method_decorator(csrf_exempt, name="dispatch")
class UnifiedSendOTPView(View):
    """Unified OTP send endpoint.

    POST /api/auth/otp/send
    Body: { email, purpose }
    Purposes: "registration", "email_verification", "password_reset"

    Returns: { message, expiresIn, purpose, resendAfter }
    """

    def post(self, request: HttpRequest) -> JsonResponse:
        data = _parse_json_body(request)
        email = data.get("email", "").strip()
        purpose = data.get("purpose", "").strip()

        if not email or not purpose:
            return error_response(
                message="Email and purpose are required",
                code="MISSING_FIELDS",
            )

        try:
            result = OTPService.unified_send_otp(
                email=email,
                purpose=purpose,
                ip_address=_get_client_ip(request),
            )
            return success_response(
                data={
                    "message": result["message"],
                    "expiresIn": result["expires_in"],
                    "purpose": result["purpose"],
                    "resendAfter": result["resend_after"],
                },
            )
        except AuthenticationError as e:
            logger.warning("Unified OTP send failed: code=%s", e.code)
            return _auth_error_response(e)


@method_decorator(csrf_exempt, name="dispatch")
class UnifiedVerifyOTPView(View):
    """Unified OTP verify endpoint.

    POST /api/auth/otp/verify
    Body: { email, code, purpose }

    Returns:
      registration/email_verification: { user, tokens, purpose }
      password_reset: { resetToken, expiresIn, purpose }
    """

    def post(self, request: HttpRequest) -> JsonResponse:
        data = _parse_json_body(request)
        email = data.get("email", "").strip()
        code = data.get("code", "").strip()
        purpose = data.get("purpose", "").strip()

        if not email or not code or not purpose:
            return error_response(
                message="Email, code, and purpose are required",
                code="MISSING_FIELDS",
            )

        try:
            result = OTPService.unified_verify_otp(
                email=email,
                code=code,
                purpose=purpose,
                ip_address=_get_client_ip(request),
            )

            # Purpose-specific response
            if purpose in ("registration", "email_verification"):
                return success_response(
                    data={
                        "user": build_user_dict(result["user"]),
                        "tokens": build_tokens_dict(
                            result["access_token"],
                            result["refresh_token"],
                        ),
                        "purpose": result["purpose"],
                    },
                )
            if purpose == "password_reset":
                return success_response(
                    data={
                        "resetToken": result["reset_token"],
                        "expiresIn": result["expires_in"],
                        "purpose": result["purpose"],
                    },
                )

        except AuthenticationError as e:
            logger.warning("Unified OTP verify failed: code=%s", e.code)
            return _auth_error_response(e)

"""Shared auth-view plumbing (JSON parsing, error mapping, base view).

Split from the former core/authentication/views.py (no behavior change).
"""

import json
import logging

from django.http import HttpRequest, JsonResponse
from django.views import View

from core.authentication.response_helpers import (
    error_response,
    success_response,
)
from core.authentication.services import AuthenticationError
from core.shared.request_utils import get_client_ip

logger = logging.getLogger("core.authentication")


DELETION_ACKNOWLEDGEMENT_KEYS = (
    "acknowledgePermanentDeletion",
    "acknowledge_permanent_deletion",
    "permanentDeletionAcknowledged",
)


def _get_client_ip(request: HttpRequest) -> str:
    """Extract client IP using the shared trusted-proxy helper."""
    return get_client_ip(request)


def _parse_json_body(request: HttpRequest) -> dict:
    """Parse JSON body from request."""
    if not request.body:
        return {}

    try:
        return json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return {}


def _auth_error_response(
    e: AuthenticationError,
    *,
    oauth_conflict_as_409: bool = False,
) -> JsonResponse:
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
        "ACCOUNT_PENDING_DELETION": 403,
        "INVALID_RECOVERY_TOKEN": 401,
        "INVALID_RECOVERY_STATE": 409,
        "RECOVERY_WINDOW_EXPIRED": 410,
        "REACTIVATION_CONFIRMATION_REQUIRED": 400,
        "DELETION_CANCELLATION_CONFIRMATION_REQUIRED": 400,
        "REAUTHENTICATION_REQUIRED": 400,
        "DELETION_ACKNOWLEDGEMENT_REQUIRED": 400,
        "INVALID_DELETION_ACKNOWLEDGEMENT": 400,
        "PASSWORD_AUTH_UNAVAILABLE": 400,
        "APPLE_KEYS_TIMEOUT": 503,
        "APPLE_KEYS_UNAVAILABLE": 503,
        "APPLE_KEYS_INVALID": 503,
        "OAUTH_NOT_CONFIGURED": 503,
        "AUTH_SERVICE_UNAVAILABLE": 503,
        "OTP_SERVICE_UNAVAILABLE": 503,
        "APPLE_TOKEN_EXPIRED": 401,
        "INVALID_OAUTH_TOKEN": 400,
        "APPLE_NONCE_REQUIRED": 400,
        "APPLE_NONCE_MISMATCH": 400,
        "APPLE_NONCE_EXPIRED": 400,
        "APPLE_PUBLIC_KEY_NOT_FOUND": 503,
    }
    status_code = status_map.get(e.code, 400)
    if oauth_conflict_as_409 and e.code in {
        "EMAIL_REGISTERED_WITH_PASSWORD",
        "EMAIL_REGISTERED_WITH_DIFFERENT_PROVIDER",
        "APPLE_ACCOUNT_MISMATCH",
    }:
        status_code = 409

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

    from core.authentication.tokens import TokenError, TokenInfrastructureError, TokenService

    try:
        payload = TokenService.validate_access_token(access_token, enforce_revocation=True)
    except TokenInfrastructureError:
        raise AuthenticationError(
            "Authentication service is temporarily unavailable. Please try again.",
            "AUTH_SERVICE_UNAVAILABLE",
        ) from None
    except TokenError:
        raise AuthenticationError("Invalid or expired token", "INVALID_TOKEN") from None

    user_id = payload.get("user_id")
    if not user_id:
        raise AuthenticationError("Invalid token payload", "INVALID_TOKEN")
    return user_id


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

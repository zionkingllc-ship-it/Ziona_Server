"""
Standardized response helpers for authentication endpoints.

Provides consistent camelCase JSON response format matching mobile team contract.
All auth views and resolvers should use these helpers instead of building
responses manually.
"""

import logging
from typing import Any

from django.http import JsonResponse

logger = logging.getLogger("core.authentication")


ERROR_STATUS_MAP: dict[str, int] = {
    "MISSING_FIELDS": 400,
    "EMAIL_ALREADY_REGISTERED": 400,
    "USERNAME_TAKEN": 400,
    "USERNAME_INVALID": 400,
    "USERNAME_LENGTH_INVALID": 400,
    "PASSWORD_WEAK": 400,
    "AGE_RESTRICTION": 400,
    "INVALID_DATE_FORMAT": 400,
    "INVALID_OTP": 400,
    "OTP_EXPIRED": 400,
    "OTP_NOT_FOUND": 400,
    "OTP_STORAGE_FAILED": 400,
    "OTP_VALIDATION_FAILED": 400,
    "EMAIL_ALREADY_VERIFIED": 400,
    "INVALID_PURPOSE": 400,
    "USER_NOT_FOUND": 400,
    "INVALID_RESET_TOKEN": 400,
    "TOKEN_VALIDATION_FAILED": 400,
    "PASSWORD_REQUIRED": 400,
    "INVALID_CREDENTIALS": 401,
    "ACCOUNT_DEACTIVATED": 401,
    "MISSING_TOKEN": 401,
    "INVALID_REFRESH_TOKEN": 401,
    "INVALID_TOKEN": 401,
    "AUTH_ERROR": 401,
    "MAX_ATTEMPTS_REACHED": 429,
    "RATE_LIMIT_EXCEEDED": 429,
    "RESEND_COOLDOWN": 429,
    "METHOD_NOT_ALLOWED": 405,
    "INTERNAL_ERROR": 500,
    "TOKEN_STORAGE_FAILED": 500,
    "SERVICE_UNAVAILABLE": 503,
}


def build_user_dict(user: Any) -> dict:
    """Build a standardized camelCase user dict from a User model instance."""
    return {
        "id": str(user.id),
        "email": user.email,
        "username": user.username,
        "role": user.role,
        "isEmailVerified": user.is_email_verified,
    }


def build_tokens_dict(access_token: str, refresh_token: str) -> dict:
    """Build a standardized camelCase tokens dict."""
    return {
        "accessToken": access_token,
        "refreshToken": refresh_token,
    }


def success_response(
    data: dict | None = None,
    status: int = 200,
) -> JsonResponse:
    """Build a standardized success JsonResponse.

    Args:
        data: Response payload (placed under "data" key).
        status: HTTP status code (default 200).

    Returns:
        JsonResponse with {success: true, data: {...}}.
    """
    body: dict[str, Any] = {"success": True}
    if data is not None:
        body["data"] = data
    return JsonResponse(body, status=status)


def error_response(
    message: str,
    code: str,
    details: dict | None = None,
    status: int | None = None,
) -> JsonResponse:
    """Build a standardized error JsonResponse.

    Args:
        message: Human-readable error message.
        code: Machine-readable error code (e.g. "INVALID_OTP").
        details: Optional additional context (e.g. attemptsRemaining).
        status: HTTP status code. If None, inferred from ERROR_STATUS_MAP.

    Returns:
        JsonResponse with {success: false, error: {message, code, details?}}.
    """
    if status is None:
        status = ERROR_STATUS_MAP.get(code, 400)

    error_body: dict[str, Any] = {
        "message": message,
        "code": code,
    }
    if details:
        error_body["details"] = details

    return JsonResponse({"success": False, "error": error_body}, status=status)


def auth_success_response(
    user: Any,
    access_token: str | None = None,
    refresh_token: str | None = None,
    message: str | None = None,
    requires_verification: bool = False,
    status: int = 200,
) -> JsonResponse:
    """Build a standardized auth success response with user + optional tokens.

    Args:
        user: User model instance.
        access_token: JWT access token (omit if requires verification).
        refresh_token: JWT refresh token (omit if requires verification).
        message: Optional message (e.g. "Check your email for OTP").
        requires_verification: Whether user needs to verify email.
        status: HTTP status code.

    Returns:
        JsonResponse with standardized auth data.
    """
    data: dict[str, Any] = {
        "user": build_user_dict(user),
    }

    if access_token and refresh_token:
        data["tokens"] = build_tokens_dict(access_token, refresh_token)

    if message:
        data["message"] = message

    if requires_verification:
        data["requiresVerification"] = True

    return success_response(data=data, status=status)

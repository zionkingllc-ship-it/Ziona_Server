"""OAuth views (Google, Apple).

Split from the former core/authentication/views.py (no behavior change).
"""

import logging

from django.http import HttpRequest, JsonResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt

from core.authentication.response_helpers import (
    build_tokens_dict,
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
                "isNewUser": result["is_new_user"],
            }
            if result.get("requires_account_recovery"):
                response_data.update(
                    {
                        "requiresAccountRecovery": True,
                        "recoveryReason": result["recovery_reason"],
                        "recoveryToken": result["recovery_token"],
                        "deletionScheduledFor": result.get("deletion_scheduled_for"),
                    }
                )
            else:
                response_data["tokens"] = build_tokens_dict(
                    result["access_token"],
                    result["refresh_token"],
                )
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
                "isNewUser": result["is_new_user"],
            }
            if result.get("requires_account_recovery"):
                response_data.update(
                    {
                        "requiresAccountRecovery": True,
                        "recoveryReason": result["recovery_reason"],
                        "recoveryToken": result["recovery_token"],
                        "deletionScheduledFor": result.get("deletion_scheduled_for"),
                    }
                )
            else:
                response_data["tokens"] = build_tokens_dict(
                    result["access_token"],
                    result["refresh_token"],
                )
            return success_response(data=response_data)
        except AuthenticationError as e:
            logger.warning("Apple OAuth failed: code=%s", e.code)
            return _auth_error_response(e, oauth_conflict_as_409=True)

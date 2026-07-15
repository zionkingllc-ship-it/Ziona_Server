"""Account lifecycle views (me, deactivate, delete, reactivate, finalize username).

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

DELETION_ACKNOWLEDGEMENT_KEYS = (
    "acknowledgePermanentDeletion",
    "acknowledge_permanent_deletion",
    "permanentDeletionAcknowledged",
)


def _account_action_payload(data: dict) -> dict[str, object]:
    """Normalize account lifecycle body fields from mobile/web clients."""
    acknowledge_permanent_deletion = False
    for key in DELETION_ACKNOWLEDGEMENT_KEYS:
        if key in data:
            acknowledge_permanent_deletion = _parse_deletion_acknowledgement(data.get(key))
            break

    return {
        "password": data.get("password") or "",
        "otp": data.get("otp") or data.get("code") or "",
        "acknowledge_permanent_deletion": acknowledge_permanent_deletion,
    }


def _deletion_scheduled_response(result: dict) -> JsonResponse:
    """Return the shared 202 contract for both deletion endpoint aliases."""
    return success_response(
        data={
            "message": "Account deletion scheduled.",
            "status": result["status"],
            "requestedAt": result["requested_at"],
            "scheduledFor": result["scheduled_for"],
            "gracePeriodDays": result["grace_period_days"],
            "canCancel": result["can_cancel"],
        },
        status=202,
    )


def _parse_deletion_acknowledgement(value: object) -> bool:
    """Parse the permanent-deletion acknowledgement as a strict boolean."""
    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1"}:
            return True
        if normalized in {"false", "0"}:
            return False

    raise AuthenticationError(
        "acknowledgePermanentDeletion must be a boolean true or false.",
        code="INVALID_DELETION_ACKNOWLEDGEMENT",
        details={
            "field": "acknowledgePermanentDeletion",
            "expected": "boolean",
            "acceptedFields": list(DELETION_ACKNOWLEDGEMENT_KEYS),
        },
    )


def _account_action_payload_from_request(
    request: HttpRequest,
    *,
    allow_query_acknowledgement: bool = False,
) -> dict[str, object]:
    """Build account-action payload from JSON body and optional DELETE query ack."""
    data = _parse_json_body(request)

    if allow_query_acknowledgement:
        for key in DELETION_ACKNOWLEDGEMENT_KEYS:
            if key not in data and key in request.GET:
                data[key] = request.GET.get(key)
                break

    return _account_action_payload(data)


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
            payload = _account_action_payload_from_request(
                request,
                allow_query_acknowledgement=True,
            )
            result = AuthService.delete_account(
                user_id=user_id,
                password=payload["password"],
                otp=payload["otp"],
                acknowledge_permanent_deletion=payload["acknowledge_permanent_deletion"],
                ip_address=_get_client_ip(request),
            )

            return _deletion_scheduled_response(result)
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
            payload = _account_action_payload_from_request(request)
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
            payload = _account_action_payload_from_request(request)
            result = AuthService.delete_account(
                user_id=user_id,
                password=payload["password"],
                otp=payload["otp"],
                acknowledge_permanent_deletion=payload["acknowledge_permanent_deletion"],
                ip_address=_get_client_ip(request),
            )
            return _deletion_scheduled_response(result)
        except AuthenticationError as e:
            logger.warning("Account deletion failed: code=%s", e.code)
            return _auth_error_response(e)


@method_decorator(csrf_exempt, name="dispatch")
class ReactivateAccountView(BaseAuthView):
    """Confirm recovery of a voluntarily deactivated account."""

    def post(self, request: HttpRequest) -> JsonResponse:
        data = _parse_json_body(request)
        recovery_token = data.get("recoveryToken") or data.get("recovery_token") or ""
        confirmed = (
            data.get("confirmReactivation") is True or data.get("confirm_reactivation") is True
        )
        try:
            result = AuthService.reactivate_account(
                recovery_token,
                confirm_reactivation=confirmed,
                ip_address=_get_client_ip(request),
            )
            return auth_success_response(
                user=result["user"],
                access_token=result["access_token"],
                refresh_token=result["refresh_token"],
                message="Account reactivated successfully.",
            )
        except AuthenticationError as e:
            return _auth_error_response(e)


@method_decorator(csrf_exempt, name="dispatch")
class CancelAccountDeletionView(BaseAuthView):
    """Cancel a pending deletion using a recovery-only login token."""

    def post(self, request: HttpRequest) -> JsonResponse:
        data = _parse_json_body(request)
        recovery_token = data.get("recoveryToken") or data.get("recovery_token") or ""
        confirmed = (
            data.get("confirmCancellation") is True or data.get("confirm_cancellation") is True
        )
        try:
            result = AuthService.cancel_account_deletion(
                recovery_token,
                confirm_cancellation=confirmed,
                ip_address=_get_client_ip(request),
            )
            return auth_success_response(
                user=result["user"],
                access_token=result["access_token"],
                refresh_token=result["refresh_token"],
                message="Account deletion cancelled.",
            )
        except AuthenticationError as e:
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
            from core.authentication.tokens import TokenService

            payload = TokenService.validate_access_token(access_token)
            user_id = payload.get("user_id")

            if not user_id:
                raise AuthenticationError("Invalid token payload", "INVALID_TOKEN")

            data = _parse_json_body(request)
            username = data.get("username", "").strip()

            if not username:
                return error_response(
                    message="Username is required",
                    code="MISSING_FIELDS",
                )

            user = AuthService.finalize_username(
                user_id=user_id,
                username=username,
            )

            return success_response(
                data={
                    "username": user.username,
                    "message": "Username updated successfully",
                }
            )

        except AuthenticationError as e:
            return _auth_error_response(e)

"""REST endpoints for hosted Stripe support flows."""

import json
import logging

from django.http import HttpRequest
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from core.authentication.response_helpers import error_response, success_response
from core.donations.hosted_services import HostedSupportService
from core.shared.exceptions import AdminError

logger = logging.getLogger("core.donations")


def _request_json(request: HttpRequest) -> dict:
    try:
        data = json.loads(request.body or b"{}")
    except (TypeError, ValueError) as exc:
        raise AdminError("Request body must be valid JSON.", "VALIDATION_ERROR") from exc
    if not isinstance(data, dict):
        raise AdminError("Request body must be a JSON object.", "VALIDATION_ERROR")
    return data


def _optional_user(request: HttpRequest, *, required: bool = False):
    auth_header = request.META.get("HTTP_AUTHORIZATION", "")
    if not auth_header:
        if required:
            raise AdminError("Authentication required.", "UNAUTHENTICATED")
        return None
    if not auth_header.startswith("Bearer "):
        raise AdminError("Invalid authorization header.", "INVALID_TOKEN")
    try:
        from core.authentication.account_status import ensure_account_can_authenticate
        from core.authentication.tokens import TokenService
        from core.users.models import User

        payload = TokenService.validate_access_token(
            auth_header[7:],
            enforce_revocation=required,
        )
        user = User.all_objects.get(id=payload["user_id"])
        ensure_account_can_authenticate(user)
        return user
    except AdminError:
        raise
    except Exception as exc:
        logger.info("support_request_invalid_token")
        raise AdminError("Invalid or expired token.", "INVALID_TOKEN") from exc


def _admin_error(exc: AdminError):
    code = str(exc.code)
    status = 401 if code in {"UNAUTHENTICATED", "INVALID_TOKEN"} else 400
    if code == "NOT_FOUND":
        status = 404
    return error_response(exc.message, code, status=status)


def _create_checkout(request: HttpRequest, donation_type: str):
    try:
        data = _request_json(request)
        result = HostedSupportService.create_checkout(
            amount_usd=data.get("amountUsd", data.get("amount")),
            donation_type=donation_type,
            email=str(data.get("email") or ""),
            name=str(data.get("name") or ""),
            user=_optional_user(request),
            idempotency_key=request.META.get("HTTP_IDEMPOTENCY_KEY", ""),
        )
        return success_response(
            {
                "transactionId": result["transaction_id"],
                "checkoutSessionId": result["checkout_session_id"],
                "checkoutUrl": result["checkout_url"],
                "status": result["status"],
                "type": result["type"],
            },
            status=201,
        )
    except AdminError as exc:
        return _admin_error(exc)


@csrf_exempt
@require_POST
def support_once(request: HttpRequest):
    return _create_checkout(request, "one_time")


@csrf_exempt
@require_POST
def support_monthly(request: HttpRequest):
    return _create_checkout(request, "monthly")


@require_GET
def checkout_status(request: HttpRequest, checkout_session_id: str):
    try:
        result = HostedSupportService.get_checkout_status(checkout_session_id)
        return success_response(
            {
                "transactionId": result["transaction_id"],
                "checkoutSessionId": result["checkout_session_id"],
                "status": result["status"],
                "type": result["type"],
                "isEarlySupporter": result["is_early_supporter"],
                "earlySupporterNumber": result["early_supporter_number"],
            }
        )
    except AdminError as exc:
        return _admin_error(exc)


@csrf_exempt
@require_POST
def customer_portal(request: HttpRequest):
    try:
        result = HostedSupportService.create_customer_portal_session(
            user=_optional_user(request, required=True)
        )
        return success_response({"portalUrl": result["portal_url"]})
    except AdminError as exc:
        return _admin_error(exc)

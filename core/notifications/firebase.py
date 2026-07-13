"""Firebase Cloud Messaging integration."""

import logging
from typing import Any

from django.conf import settings

try:
    import firebase_admin
    from firebase_admin import credentials, messaging
except ImportError:
    firebase_admin = None
    credentials = None
    messaging = None

from core.notifications.models import DeviceToken

logger = logging.getLogger(__name__)

_firebase_initialized = False


def initialize_firebase():
    """Load credentials and initialize Firebase Admin SDK."""
    global _firebase_initialized
    if _firebase_initialized or firebase_admin is None:
        return

    try:
        cred_path = getattr(settings, "FIREBASE_CREDENTIALS_FILE", None)
        project_id = getattr(settings, "FIREBASE_PROJECT_ID", None)

        if cred_path:
            cred = credentials.Certificate(cred_path)
            options = {"projectId": project_id} if project_id else None

            # Prevent double initialization error
            if not firebase_admin._apps:
                firebase_admin.initialize_app(cred, options)
            _firebase_initialized = True
            logger.info(
                "firebase_admin_initialized",
                extra={"project_id": project_id or "default"},
            )
        else:
            logger.warning("FIREBASE_CREDENTIALS_FILE not set. Push notifications will fail.")
    except Exception as e:
        logger.error(f"Failed to initialize Firebase: {e}", exc_info=True)


def send_fcm_notification(
    tokens: list[str], title: str, body: str, data: dict[str, Any]
) -> dict[str, int]:
    """
    Send push notification via FCM using multicast.

    Tokens are sent in chunks of at most 500 to respect the FCM hard limit.
    Each chunk is dispatched independently so a failure in one chunk does not
    prevent the remaining chunks from being sent.
    """
    summary = {"success_count": 0, "failure_count": 0, "invalid_token_count": 0}
    if not tokens:
        return summary

    if firebase_admin is None:
        logger.error("Cannot send FCM message: firebase-admin package not installed.")
        return summary

    initialize_firebase()
    if not _firebase_initialized:
        logger.error("Cannot send FCM message: Firebase not initialized.")
        return summary

    # Ensure data values are strings as required by FCM
    formatted_data = {str(k): str(v) for k, v in data.items() if v is not None}

    # FCM hard limit: a single MulticastMessage may not contain more than 500
    # registration tokens.  We chunk the list and send each slice separately.
    fcm_chunk_size = 500
    all_invalid_tokens: list[str] = []

    for chunk_start in range(0, len(tokens), fcm_chunk_size):
        chunk = tokens[chunk_start : chunk_start + fcm_chunk_size]

        message = messaging.MulticastMessage(
            notification=messaging.Notification(
                title=title,
                body=body,
            ),
            data=formatted_data,
            tokens=chunk,
        )

        try:
            response = messaging.send_each_for_multicast(message)
            summary["success_count"] += response.success_count
            summary["failure_count"] += response.failure_count
            logger.info(
                "fcm_chunk_sent",
                extra={
                    "chunk_start": chunk_start,
                    "chunk_size": len(chunk),
                    "success_count": response.success_count,
                    "failure_count": response.failure_count,
                },
            )

            if response.failure_count > 0:
                for i, result in enumerate(response.responses):
                    if not result.success:
                        err_code = getattr(result.exception, "code", "UNKNOWN")
                        if err_code in [
                            "NOT_FOUND",
                            "INVALID_ARGUMENT",
                            "messaging/invalid-registration-token",
                            "messaging/registration-token-not-registered",
                        ]:
                            all_invalid_tokens.append(chunk[i])

        except Exception as e:
            logger.error(
                f"Failed to send FCM chunk [{chunk_start}:{chunk_start + len(chunk)}]: {e}",
                exc_info=True,
            )

    if all_invalid_tokens:
        DeviceToken.objects.filter(token__in=all_invalid_tokens).update(is_active=False)
        summary["invalid_token_count"] = len(all_invalid_tokens)
        logger.info(
            "fcm_invalid_tokens_deactivated",
            extra={"invalid_token_count": len(all_invalid_tokens)},
        )

    return summary


def get_fcm_project_id() -> str:
    """Return the Firebase project id the backend is actually wired to.

    Lets the debug tooling confirm the app is being configured against the
    same project (e.g. `ziona-app`). Reads it from the initialized app rather
    than settings so it reflects the real loaded credentials.
    """
    if firebase_admin is None:
        return ""
    initialize_firebase()
    try:
        return firebase_admin.get_app().project_id or ""
    except Exception:
        return getattr(settings, "FIREBASE_PROJECT_ID", "") or ""


def send_fcm_debug(
    tokens: list[str], title: str, body: str, data: dict[str, Any]
) -> list[dict[str, Any]]:
    """Send a test push and return raw per-token FCM results, WITHOUT side effects.

    Unlike send_fcm_notification(), this never deactivates tokens — it is a
    diagnostic helper so the caller can read FCM's exact accept/reject reason
    and retry the same token after fixing the client. Results are returned in
    the same order as `tokens` (1:1). Every failure mode (SDK missing, init
    failure, transport error) is reported as a per-token result, never raised.
    """
    if not tokens:
        return []

    def _all(error_code: str, error_message: str) -> list[dict[str, Any]]:
        return [
            {
                "success": False,
                "message_id": None,
                "error_code": error_code,
                "error_message": error_message,
            }
            for _ in tokens
        ]

    if firebase_admin is None:
        return _all("SDK_NOT_INSTALLED", "firebase-admin package not installed")

    initialize_firebase()
    if not _firebase_initialized:
        return _all("FIREBASE_NOT_INITIALIZED", "Firebase Admin SDK failed to initialize")

    formatted_data = {str(k): str(v) for k, v in data.items() if v is not None}
    message = messaging.MulticastMessage(
        notification=messaging.Notification(title=title, body=body),
        data=formatted_data,
        tokens=tokens,
    )

    try:
        response = messaging.send_each_for_multicast(message)
    except Exception as exc:
        return _all("SEND_FAILED", str(exc))

    results: list[dict[str, Any]] = []
    for result in response.responses:
        if result.success:
            results.append(
                {
                    "success": True,
                    "message_id": result.message_id,
                    "error_code": None,
                    "error_message": None,
                }
            )
        else:
            exc = result.exception
            results.append(
                {
                    "success": False,
                    "message_id": None,
                    "error_code": getattr(exc, "code", "UNKNOWN"),
                    "error_message": str(exc) if exc else "unknown error",
                }
            )
    return results

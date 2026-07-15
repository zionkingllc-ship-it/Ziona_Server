"""User-facing moderation notifications + emails (warn/suspend/delete copy).

Split from core/admin_dashboard/user_services.py (no behavior change).
"""

import logging

from django.db import transaction

logger = logging.getLogger("core.admin_dashboard")


def _moderation_notification_copy(action_type: str, reason: str) -> tuple[str, str]:
    if action_type == "warned":
        return (
            "Community Warning",
            "We noticed activity on your account that may violate Ziona's community "
            f'guidelines.\n\nReason:\n"{reason}"\n\n'
            "Please review our guidelines and avoid repeated violations.",
        )

    if action_type == "suspended":
        return (
            "Account Suspended",
            "Your Ziona account has been suspended.\n\n"
            f'Reason:\n"{reason}"\n\n'
            "If you believe this was a mistake, contact support@ziona.app.",
        )

    if action_type == "reactivated":
        return (
            "Account Reactivated",
            "Your Ziona account has been reactivated and you can now log in again.",
        )

    return ("Ziona Account Update", reason)


def _moderation_email_copy(action_type: str, reason: str) -> tuple[str, str]:
    if action_type == "warned":
        return (
            "Community Warning",
            "Hello,\n\n"
            "We noticed activity on your account that may violate Ziona's community "
            "guidelines.\n\n"
            f'Reason for warning:\n"{reason}"\n\n'
            "This warning does not restrict your account access at this time.\n"
            "Please review our community guidelines and avoid repeated violations to "
            "maintain a safe and faith-aligned environment for everyone.\n\n"
            "If you believe this was sent in error, you can contact us at "
            "support@ziona.app.\n\n"
            "- Ziona Team",
        )

    if action_type == "suspended":
        return (
            "Your Ziona Account Has Been Suspended",
            "Hello,\n\n"
            "Your Ziona account has been suspended due to a violation of our community "
            "guidelines.\n\n"
            f'Reason for suspension:\n"{reason}"\n\n'
            "While suspended, you will not be able to interact with content, post, "
            "comment, or access your account.\n\n"
            "If you believe this action was taken in error or would like to appeal, "
            "please contact:\nsupport@ziona.app\n\n"
            "- Ziona Team",
        )

    if action_type == "reactivated":
        return (
            "Your Ziona Account Has Been Reactivated",
            "Hello,\n\n"
            "Your Ziona account has been reactivated and you can now log in again.\n\n"
            "Please continue to follow our community guidelines to help maintain a "
            "safe and respectful environment for everyone.\n\n"
            "- Ziona Team",
        )

    return ("Ziona Account Update", reason)


def _queue_moderation_email(user, action_type: str, reason: str) -> None:
    if not user.email:
        return

    email = user.email
    subject, message = _moderation_email_copy(action_type, reason)

    def _send():
        try:
            from django.conf import settings

            from core.shared.tasks.email_tasks import queue_email_delivery

            queue_email_delivery(
                subject=subject,
                message=message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email],
                email_kind=f"moderation_{action_type}",
            )
        except Exception:
            logger.warning(
                "Failed to queue moderation email",
                extra={"user_id": str(user.id), "action_type": action_type},
                exc_info=True,
            )

    transaction.on_commit(_send)


def _notify_user_moderation(user, action_type: str, reason: str):
    """Send an in-app notification about a moderation action."""
    try:
        from core.notifications.services import create_notification

        title, message = _moderation_notification_copy(action_type, reason)

        create_notification(
            user_id=user.id,
            type_str="admin_announcement",
            reference_id=user.id,
            reference_type="User",
            title=title,
            message=message,
            respect_preferences=False,
            bypass_duplicate_check=True,
        )
    except Exception:
        logger.warning("Failed to send moderation notification", exc_info=True)

"""
EmailService — facade over all platform email sends.

Every method:
 - Builds (subject, plain, html) via templates.py
 - Dispatches via send_email_async.delay() — never blocks a request
 - Logs all sends; never raises on failure (graceful degradation)
 - Routes internal contact emails per brand
"""

import logging

from django.conf import settings

logger = logging.getLogger("core.emails")


class EmailService:
    """Central email dispatch facade for the Ziona platform."""

    # ── Template 1 ────────────────────────────────────────────
    @staticmethod
    def send_verify_email(user_name: str | None, email: str, otp_code: str) -> None:
        """Send email verification OTP. Trigger: on user registration."""
        from core.emails.templates import render_verify_email
        from core.shared.tasks.email_tasks import send_email_async

        try:
            subject, plain, html = render_verify_email(user_name, otp_code)
            send_email_async.delay(
                subject=subject,
                message=plain,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email],
                html_message=html,
            )
            logger.info("verify_email_queued", extra={"email": email})
        except Exception:  # noqa: BLE001 — Celery broker can raise arbitrary errors
            logger.error("Failed to queue verify_email", extra={"email": email}, exc_info=True)

    # ── Template 2 ────────────────────────────────────────────
    @staticmethod
    def send_reset_password(user_name: str | None, email: str, otp_code: str) -> None:
        """Send password reset OTP. Trigger: POST /auth/forgot-password."""
        from core.emails.templates import render_reset_password
        from core.shared.tasks.email_tasks import send_email_async

        try:
            subject, plain, html = render_reset_password(user_name, otp_code)
            send_email_async.delay(
                subject=subject,
                message=plain,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email],
                html_message=html,
            )
            logger.info("reset_password_email_queued", extra={"email": email})
        except Exception:  # noqa: BLE001
            logger.error(
                "Failed to queue reset_password email", extra={"email": email}, exc_info=True
            )

    # ── Template 3 ────────────────────────────────────────────
    @staticmethod
    def send_welcome_email(user_name: str | None, email: str) -> None:
        """Send welcome email. Trigger: on successful email verification."""
        from core.emails.templates import render_welcome_email
        from core.shared.tasks.email_tasks import send_email_async

        try:
            subject, plain, html = render_welcome_email(user_name)
            send_email_async.delay(
                subject=subject,
                message=plain,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email],
                html_message=html,
            )
            logger.info("welcome_email_queued", extra={"email": email})
        except Exception:  # noqa: BLE001
            logger.error("Failed to queue welcome email", extra={"email": email}, exc_info=True)

    # ── Template 4 ────────────────────────────────────────────
    @staticmethod
    def send_notification_digest(
        user_name: str | None,
        email: str,
        activities: list[dict] | None = None,
    ) -> None:
        """Send daily notification digest.

        Guards:
         - activities None → treated as []
         - Empty activities → log and return (never send empty digest)
        """
        from core.emails.templates import render_notification_digest
        from core.shared.tasks.email_tasks import send_email_async

        safe_activities = activities or []
        if not safe_activities:
            logger.debug("notification_digest_skipped_no_activity", extra={"email": email})
            return

        try:
            subject, plain, html = render_notification_digest(user_name, safe_activities)
            send_email_async.delay(
                subject=subject,
                message=plain,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email],
                html_message=html,
            )
            logger.info(
                "digest_email_queued",
                extra={"email": email, "activity_count": len(safe_activities)},
            )
        except Exception:  # noqa: BLE001
            logger.error("Failed to queue digest email", extra={"email": email}, exc_info=True)

    @staticmethod
    def send_admin_announcement(
        user_name: str | None,
        email: str,
        heading: str,
        body: str,
        circle_name: str = "Ziona",
        published_at: str | None = None,
        cta_label: str = "Open Ziona",
        cta_link: str | None = None,
    ) -> None:
        """Send an admin announcement email to one recipient."""
        from core.emails.templates import render_admin_announcement
        from core.shared.tasks.email_tasks import send_email_async

        try:
            subject, plain, html = render_admin_announcement(
                user_name=user_name,
                heading=heading,
                body=body,
                circle_name=circle_name,
                published_at=published_at,
                cta_label=cta_label,
                cta_link=cta_link,
            )
            send_email_async.delay(
                subject=subject,
                message=plain,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email],
                html_message=html,
            )
            logger.info("admin_announcement_email_queued", extra={"email": email})
        except Exception:  # noqa: BLE001
            logger.error(
                "Failed to queue admin announcement email",
                extra={"email": email},
                exc_info=True,
            )

    @staticmethod
    def send_support_donation_email(
        user_name: str | None,
        email: str,
        support_amount: str,
        support_date: str | None = None,
    ) -> None:
        """Send donation/support confirmation email."""
        from core.emails.templates import render_support_donation
        from core.shared.tasks.email_tasks import send_email_async

        try:
            subject, plain, html = render_support_donation(
                user_name=user_name,
                support_amount=support_amount,
                support_date=support_date,
            )
            send_email_async.delay(
                subject=subject,
                message=plain,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email],
                html_message=html,
            )
            logger.info("support_donation_email_queued", extra={"email": email})
        except Exception:  # noqa: BLE001
            logger.error(
                "Failed to queue support donation email",
                extra={"email": email},
                exc_info=True,
            )

    # ── Template 5 ────────────────────────────────────────────
    @staticmethod
    def send_waitlist_confirmation(email: str, brand: str = "ZIONA") -> None:
        """Send waitlist confirmation. Trigger: successful joinWaitlist."""
        from core.emails.templates import _brand, render_waitlist_confirmation
        from core.shared.tasks.email_tasks import send_email_async

        try:
            b = _brand(brand)
            subject, plain, html = render_waitlist_confirmation(email, brand)
            send_email_async.delay(
                subject=subject,
                message=plain,
                from_email=b["from_email"],
                recipient_list=[email],
                html_message=html,
            )
            logger.info("waitlist_email_queued", extra={"email": email, "brand": brand})
        except Exception:  # noqa: BLE001
            logger.error("Failed to queue waitlist email", extra={"email": email}, exc_info=True)

    # ── Template 6 ────────────────────────────────────────────
    @staticmethod
    def send_contact_auto_reply(name: str | None, email: str, brand: str = "ZIONA") -> None:
        """Send auto-reply to contact form submitter."""
        from core.emails.templates import _brand, render_contact_auto_reply
        from core.shared.tasks.email_tasks import send_email_async

        try:
            b = _brand(brand)
            subject, plain, html = render_contact_auto_reply(name, brand)
            send_email_async.delay(
                subject=subject,
                message=plain,
                from_email=b["from_email"],
                recipient_list=[email],
                html_message=html,
            )
            logger.info("contact_auto_reply_queued", extra={"email": email, "brand": brand})
        except Exception:  # noqa: BLE001
            logger.error(
                "Failed to queue contact auto-reply", extra={"email": email}, exc_info=True
            )

    # ── Template 7 ────────────────────────────────────────────
    @staticmethod
    def send_internal_contact_notification(
        name: str | None,
        email: str,
        message: str,
        brand: str = "ZIONA",
    ) -> None:
        """Route internal contact notification to the correct support inbox.

        ZIONA    → settings.ZIONA_SUPPORT_EMAIL   (support@ziona.app)
        ZIONKING → settings.ZIONKING_CONTACT_EMAIL (info@zionking.org)
        """
        from core.emails.templates import _brand, render_contact_internal_notification
        from core.shared.tasks.email_tasks import send_email_async

        brand_upper = brand.upper()
        if brand_upper == "ZIONKING":
            internal_recipient = getattr(settings, "ZIONKING_CONTACT_EMAIL", "info@zionking.org")
        else:
            internal_recipient = getattr(settings, "ZIONA_SUPPORT_EMAIL", "support@ziona.app")

        try:
            b = _brand(brand)
            subject, plain, html = render_contact_internal_notification(name, email, message, brand)
            send_email_async.delay(
                subject=subject,
                message=plain,
                from_email=b["from_email"],
                recipient_list=[internal_recipient],
                html_message=html,
            )
            logger.info(
                "internal_contact_notification_queued",
                extra={"brand": brand, "to": internal_recipient},
            )
        except Exception:  # noqa: BLE001
            logger.error(
                "Failed to queue internal contact notification",
                extra={"brand": brand},
                exc_info=True,
            )

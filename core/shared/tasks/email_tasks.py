"""Celery tasks for email sending.

This module contains background tasks for sending emails asynchronously
to prevent blocking API responses.
"""

import logging

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    retry_backoff=True,
    retry_backoff_max=900,
    retry_jitter=True,
)
def send_email_async(
    self,
    subject: str,
    message: str,
    from_email: str | None,
    recipient_list: list[str],
    html_message: str | None = None,
) -> dict:
    """Send an email asynchronously via Celery.

    Supports both plain-text and HTML emails. When `html_message` is provided
    the email is sent as a multipart/alternative message (plain + HTML) so
    the Ensend backend receives a proper HTML body.

    Retries automatically with exponential back-off:
    - Retry 1: 1 minute
    - Retry 2: ~5 minutes
    - Retry 3: ~15 minutes

    Args:
        subject: Email subject line.
        message: Plain-text email body (always required as fallback).
        from_email: Sender email address (uses DEFAULT_FROM_EMAIL if None).
        recipient_list: List of recipient email addresses.
        html_message: Optional HTML body. When supplied, sent as
            multipart/alternative alongside the plain-text body.

    Returns:
        dict with 'success' and 'message' keys.
    """
    try:
        sender = from_email or settings.DEFAULT_FROM_EMAIL

        if html_message:
            from django.core.mail import EmailMultiAlternatives

            msg = EmailMultiAlternatives(
                subject=subject,
                body=message,
                from_email=sender,
                to=recipient_list,
            )
            msg.attach_alternative(html_message, "text/html")
            msg.send(fail_silently=False)
        else:
            send_mail(
                subject=subject,
                message=message,
                from_email=sender,
                recipient_list=recipient_list,
                fail_silently=False,
            )

        logger.info(
            "email_sent",
            extra={
                "subject": subject,
                "recipients": recipient_list,
                "html": bool(html_message),
                "task_id": self.request.id,
            },
        )
        return {"success": True, "message": "Email sent"}

    except Exception as exc:
        logger.warning(
            f"Email send failed (attempt {self.request.retries + 1}/3): {exc}",
            extra={
                "subject": subject,
                "recipients": recipient_list,
                "task_id": self.request.id,
                "error": str(exc),
            },
        )

        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            logger.error(
                f"Email failed after 3 retries: {subject}",
                extra={
                    "subject": subject,
                    "recipients": recipient_list,
                    "task_id": self.request.id,
                    "error": str(exc),
                },
                exc_info=True,
            )
            return {"success": False, "message": f"Failed after retries: {exc}"}

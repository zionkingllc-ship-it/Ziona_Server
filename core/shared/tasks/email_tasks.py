"""Celery tasks for email sending.

This module contains background tasks for sending emails asynchronously
to prevent blocking API responses.
"""

import logging
import time
from datetime import datetime

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

logger = logging.getLogger(__name__)

_DEFAULT_EMAIL_SUBJECT = "Ziona Update"
_MIN_PROVIDER_SUBJECT_LENGTH = 5


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
    enqueued_at: str | None = None,
    email_kind: str | None = None,
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
    safe_subject = _normalise_subject(subject)
    delivery_info = getattr(self.request, "delivery_info", {}) or {}
    queue_name = delivery_info.get("routing_key") or delivery_info.get("exchange")
    queue_delay_ms = _compute_queue_delay_ms(enqueued_at)
    logger.info(
        "email_task_started",
        extra={
            "subject": safe_subject,
            "recipients": recipient_list,
            "task_id": self.request.id,
            "queue": queue_name,
            "queue_delay_ms": queue_delay_ms,
            "email_kind": email_kind,
        },
    )
    provider_started = time.monotonic()

    try:
        sender = from_email or settings.DEFAULT_FROM_EMAIL
        if safe_subject != subject:
            logger.warning(
                "email_subject_normalized",
                extra={
                    "original_subject": subject,
                    "subject": safe_subject,
                    "recipients": recipient_list,
                    "task_id": self.request.id,
                },
            )

        if html_message:
            from django.core.mail import EmailMultiAlternatives

            msg = EmailMultiAlternatives(
                subject=safe_subject,
                body=message,
                from_email=sender,
                to=recipient_list,
            )
            msg.attach_alternative(html_message, "text/html")
            sent_count = msg.send(fail_silently=False)
        else:
            sent_count = send_mail(
                subject=safe_subject,
                message=message,
                from_email=sender,
                recipient_list=recipient_list,
                fail_silently=False,
            )

        if sent_count <= 0:
            raise RuntimeError("Email backend accepted request but sent 0 messages")

        logger.info(
            "email_sent",
            extra={
                "subject": safe_subject,
                "recipients": recipient_list,
                "html": bool(html_message),
                "sent_count": sent_count,
                "task_id": self.request.id,
                "queue": queue_name,
                "queue_delay_ms": queue_delay_ms,
                "provider_duration_ms": round((time.monotonic() - provider_started) * 1000, 2),
                "email_kind": email_kind,
            },
        )
        return {"success": True, "message": "Email sent"}

    except Exception as exc:
        logger.warning(
            f"Email send failed (attempt {self.request.retries + 1}/3): {exc}",
            extra={
                "subject": safe_subject,
                "recipients": recipient_list,
                "task_id": self.request.id,
                "error": str(exc),
                "queue": queue_name,
                "queue_delay_ms": queue_delay_ms,
                "provider_duration_ms": round((time.monotonic() - provider_started) * 1000, 2),
                "email_kind": email_kind,
            },
        )

        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            logger.error(
                f"Email failed after 3 retries: {safe_subject}",
                extra={
                    "subject": safe_subject,
                    "recipients": recipient_list,
                    "task_id": self.request.id,
                    "error": str(exc),
                    "queue": queue_name,
                    "queue_delay_ms": queue_delay_ms,
                    "provider_duration_ms": round((time.monotonic() - provider_started) * 1000, 2),
                    "email_kind": email_kind,
                },
                exc_info=True,
            )
            return {"success": False, "message": f"Failed after retries: {exc}"}


def queue_email_delivery(
    *,
    subject: str,
    message: str,
    from_email: str | None,
    recipient_list: list[str],
    html_message: str | None = None,
    email_kind: str | None = None,
):
    """Publish email work onto the highest-priority queue."""
    enqueued_at = timezone.now().isoformat()
    async_result = send_email_async.apply_async(
        kwargs={
            "subject": subject,
            "message": message,
            "from_email": from_email,
            "recipient_list": recipient_list,
            "html_message": html_message,
            "enqueued_at": enqueued_at,
            "email_kind": email_kind,
        },
        queue=settings.CELERY_QUEUE_EMAIL,
        priority=settings.CELERY_EMAIL_TASK_PRIORITY,
    )
    logger.info(
        "email_queued",
        extra={
            "subject": subject,
            "recipients": recipient_list,
            "task_id": async_result.id,
            "queue": settings.CELERY_QUEUE_EMAIL,
            "priority": settings.CELERY_EMAIL_TASK_PRIORITY,
            "email_kind": email_kind,
            "enqueued_at": enqueued_at,
        },
    )
    return async_result


def _normalise_subject(subject: str | None) -> str:
    """Return a provider-safe subject for Ensend/SMTPexpress.

    Ensend rejects subjects shorter than five characters. Normalising here keeps
    one-off callers and old queued tasks from causing repeated Celery retries.
    """
    cleaned = (subject or "").strip()
    if len(cleaned) >= _MIN_PROVIDER_SUBJECT_LENGTH:
        return cleaned
    if cleaned:
        return f"{cleaned} - Ziona"
    return _DEFAULT_EMAIL_SUBJECT


def _compute_queue_delay_ms(enqueued_at: str | None) -> float | None:
    """Return queue wait time in milliseconds when a publish timestamp is available."""
    if not enqueued_at:
        return None

    try:
        published_at = datetime.fromisoformat(enqueued_at)
    except ValueError:
        logger.debug("email_task_invalid_enqueue_timestamp", extra={"enqueued_at": enqueued_at})
        return None

    if timezone.is_naive(published_at):
        published_at = timezone.make_aware(published_at, timezone.utc)

    return round((timezone.now() - published_at).total_seconds() * 1000, 2)

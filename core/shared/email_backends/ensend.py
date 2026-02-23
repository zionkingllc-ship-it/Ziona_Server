import logging
from typing import Any

import requests
from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail.message import EmailMessage

logger = logging.getLogger("core.shared.email")

_DEFAULT_API_URL = "https://api.smtpexpress.com/send"
_DEFAULT_TIMEOUT = 10  


class EnsendEmailBackend(BaseEmailBackend):
    """Django email backend that sends via the Ensend REST API.

    Settings consumed (from ``django.conf.settings``):
        ENSEND_API_KEY      – Project secret (Bearer token).
        ENSEND_API_URL      – API endpoint (default: smtpexpress.com/send).
        ENSEND_SENDER_NAME  – Default sender display name.
        DEFAULT_FROM_EMAIL  – Sender email address.

    Usage in settings:
        EMAIL_BACKEND = "core.shared.email_backends.ensend.EnsendEmailBackend"
    """

    def __init__(self, fail_silently: bool = False, **kwargs: Any) -> None:
        super().__init__(fail_silently=fail_silently, **kwargs)
        self.api_key: str = getattr(settings, "ENSEND_API_KEY", "")
        self.api_url: str = getattr(settings, "ENSEND_API_URL", _DEFAULT_API_URL)
        self.sender_name: str = getattr(settings, "ENSEND_SENDER_NAME", "Ziona Team")
        self.sender_email: str = getattr(
            settings, "DEFAULT_FROM_EMAIL", "noreply@ziona.app"
        )
        self._session: requests.Session | None = None


    def open(self) -> bool:
        """Open a reusable HTTP session."""
        if self._session is not None:
            return False  

        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )
        return True

    def close(self) -> None:
        """Close the HTTP session."""
        if self._session is not None:
            self._session.close()
            self._session = None


    def send_messages(self, email_messages: list[EmailMessage]) -> int:
        """Send one or more EmailMessage objects via Ensend.

        Args:
            email_messages: List of Django EmailMessage objects.

        Returns:
            Number of messages sent successfully.
        """
        if not self.api_key:
            logger.error(
                "ENSEND_API_KEY is not configured — cannot send emails. "
                "Set it in your environment or Django settings."
            )
            if not self.fail_silently:
                raise ValueError("ENSEND_API_KEY is not configured")
            return 0

        if not email_messages:
            return 0

        new_session = self.open()
        sent_count = 0

        try:
            for message in email_messages:
                try:
                    if self._send_single(message):
                        sent_count += 1
                except Exception as exc:
                    logger.error(
                        "Failed to send email via Ensend",
                        extra={
                            "subject": message.subject,
                            "recipients": message.to,
                            "error": str(exc),
                        },
                    )
                    if not self.fail_silently:
                        raise
        finally:
            if new_session:
                self.close()

        return sent_count


    def _send_single(self, message: EmailMessage) -> bool:
        """Send a single EmailMessage via the Ensend API.

        Args:
            message: Django EmailMessage instance.

        Returns:
            True if the API accepted the message.

        Raises:
            requests.HTTPError: On non-2xx response (unless fail_silently).
        """

        html_body = self._extract_html(message)
        text_body = message.body

        body_content = html_body or text_body

        from_email = message.from_email or self.sender_email

        payload = {
            "subject": message.subject,
            "message": body_content,
            "sender": {
                "name": self.sender_name,
                "email": from_email,
            },
            "recipients": ", ".join(message.to),
        }

        assert self._session is not None  
        response = self._session.post(
            self.api_url,
            json=payload,
            timeout=_DEFAULT_TIMEOUT,
        )

        if response.status_code == 429:
            logger.warning(
                "Ensend rate limit hit (HTTP 429). "
                "Email not sent — will not retry automatically.",
                extra={
                    "subject": message.subject,
                    "recipients": message.to,
                },
            )
            return False

        if not response.ok:
            error_detail = response.text[:500]
            logger.error(
                "Ensend API error %s: %s",
                response.status_code,
                error_detail,
            )
            response.raise_for_status() 

        logger.info(
            "Email sent via Ensend",
            extra={
                "subject": message.subject,
                "recipients": message.to,
                "status_code": response.status_code,
            },
        )
        return True

    @staticmethod
    def _extract_html(message: EmailMessage) -> str | None:
        """Extract HTML content from an EmailMessage.

        Works with both plain EmailMessage and EmailMultiAlternatives.

        Returns:
            HTML string, or None if no HTML alternative exists.
        """
        
        alternatives = getattr(message, "alternatives", None)
        if alternatives:
            for content, mimetype in alternatives:
                if mimetype == "text/html":
                    return content
        return None

"""Scheduled authentication maintenance tasks."""

import logging

from celery import shared_task

from core.authentication.tokens import TokenService

logger = logging.getLogger("core.authentication")


@shared_task(bind=True, max_retries=2, default_retry_delay=60, soft_time_limit=120, time_limit=180)
def cleanup_inactive_refresh_tokens(self) -> int:
    """Revoke refresh tokens that have exceeded the inactivity window."""
    try:
        return TokenService.cleanup_inactive_refresh_tokens()
    except Exception as exc:  # noqa: BLE001
        logger.error("inactive_refresh_token_cleanup_failed", exc_info=True)
        raise self.retry(exc=exc) from exc

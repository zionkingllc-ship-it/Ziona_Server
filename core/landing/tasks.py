"""Celery tasks for the landing module."""

import logging

from celery import shared_task

logger = logging.getLogger("core.landing")


@shared_task(bind=True, max_retries=3, default_retry_delay=60, soft_time_limit=120)
def refresh_company_stats(self) -> None:
    """Refresh company stats from live DB counts. Runs hourly via Celery Beat."""
    try:
        from core.landing.services import CompanyStatService

        CompanyStatService.refresh()
        logger.info("refresh_company_stats_complete")
    except Exception as exc:
        logger.error(
            f"refresh_company_stats failed (attempt {self.request.retries + 1}): {exc}",
            exc_info=True,
        )
        raise self.retry(exc=exc, countdown=60 * (2**self.request.retries)) from exc

"""
Celery tasks for the admin dashboard.

All tasks are idempotent and retryable. Background jobs use DB-level locks
(select_for_update with skip_locked) to prevent double-execution.
"""

import logging
from datetime import datetime, timedelta, timezone

from celery import shared_task

logger = logging.getLogger("core.admin_dashboard")


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(Exception,),
    retry_backoff=True,
    acks_late=True,
)
def post_scheduled_anchor(self, anchor_id: str):
    """Post a scheduled anchor when its scheduled time arrives.

    Idempotent: checks anchor_status != 'scheduled' and returns early if already posted.
    Uses select_for_update to prevent double-posting from concurrent workers.
    """
    from django.db import transaction

    from core.circles.models import Anchor

    with transaction.atomic():
        anchor = (
            Anchor.objects.select_for_update().filter(id=anchor_id, deleted_at__isnull=True).first()
        )

        if not anchor:
            logger.warning(
                "post_scheduled_anchor: Anchor not found", extra={"anchor_id": anchor_id}
            )
            return

        # Idempotency check — if already posted, do nothing
        if anchor.anchor_status != "scheduled":
            logger.info(
                "post_scheduled_anchor: Already processed",
                extra={"anchor_id": anchor_id, "status": anchor.anchor_status},
            )
            return

        now = datetime.now(timezone.utc)
        anchor.anchor_status = "posted"
        anchor.posted_at = now
        anchor.published_at = now
        anchor.expires_at = now + timedelta(hours=24)
        anchor.celery_task_id = ""
        anchor.save(
            update_fields=[
                "anchor_status",
                "posted_at",
                "published_at",
                "expires_at",
                "celery_task_id",
                "updated_at",
            ]
        )

    # Trigger notifications outside the transaction to avoid holding the lock
    try:
        from core.admin_dashboard.anchor_services import _notify_circle_members

        _notify_circle_members(anchor)
    except Exception:
        logger.warning("Failed to notify circle members post-schedule", exc_info=True)

    # Schedule expiry
    expire_anchor.apply_async(
        args=[anchor_id],
        eta=anchor.expires_at,
    )

    logger.info("anchor_posted_by_schedule", extra={"anchor_id": anchor_id})


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(Exception,),
    retry_backoff=True,
    acks_late=True,
)
def expire_anchor(self, anchor_id: str):
    """Expire an anchor after its duration elapses.

    Idempotent: checks anchor_status == 'posted' before expiring.
    """
    from django.db import transaction

    from core.circles.models import Anchor

    with transaction.atomic():
        anchor = (
            Anchor.objects.select_for_update().filter(id=anchor_id, deleted_at__isnull=True).first()
        )

        if not anchor:
            return

        if anchor.anchor_status != "posted":
            logger.info(
                "expire_anchor: Not in posted status",
                extra={"anchor_id": anchor_id, "status": anchor.anchor_status},
            )
            return

        anchor.anchor_status = "expired"
        anchor.save(update_fields=["anchor_status", "updated_at"])

    logger.info("anchor_expired", extra={"anchor_id": anchor_id})


@shared_task(
    bind=True,
    max_retries=1,
    acks_late=True,
)
def check_scheduled_anchors(self):
    """Beat task (every minute): find and post overdue scheduled anchors.

    Uses skip_locked to prevent multiple workers from processing the same anchor.
    This is a safety net — the primary mechanism is the ETA-based post_scheduled_anchor task.
    """
    from django.db import transaction

    from core.circles.models import Anchor

    now = datetime.now(timezone.utc)

    with transaction.atomic():
        overdue_anchors = Anchor.objects.select_for_update(skip_locked=True).filter(
            anchor_status="scheduled",
            scheduled_for__lte=now,
            deleted_at__isnull=True,
        )

        for anchor in overdue_anchors:
            anchor.anchor_status = "posted"
            anchor.posted_at = now
            anchor.published_at = now
            anchor.expires_at = now + timedelta(hours=24)
            anchor.celery_task_id = ""
            anchor.save(
                update_fields=[
                    "anchor_status",
                    "posted_at",
                    "published_at",
                    "expires_at",
                    "celery_task_id",
                    "updated_at",
                ]
            )

            # Schedule expiry
            expire_anchor.apply_async(args=[str(anchor.id)], eta=anchor.expires_at)

            logger.info(
                "overdue_anchor_posted",
                extra={"anchor_id": str(anchor.id)},
            )

    logger.info("check_scheduled_anchors_complete")


@shared_task(
    bind=True,
    max_retries=2,
    default_retry_delay=120,
    autoretry_for=(Exception,),
    acks_late=True,
)
def calculate_daily_analytics(self):
    """Beat task (00:05 UTC daily): aggregate previous day's metrics.

    Idempotent: uses update_or_create keyed on date, so retries are safe.
    """

    from django.utils import timezone

    from core.admin_dashboard.models import DailyAnalytics
    from core.engagement.models import Comment
    from core.moderation.models import Report, ReportStatus
    from core.posts.models import Post
    from core.users.models import User

    yesterday = timezone.now().date() - timedelta(days=1)
    day_start = datetime.combine(yesterday, datetime.min.time()).replace(tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)

    total_users = User.objects.filter(
        deleted_at__isnull=True,
        created_at__lt=day_end,
    ).count()

    new_users = User.objects.filter(
        deleted_at__isnull=True,
        created_at__gte=day_start,
        created_at__lt=day_end,
    ).count()

    dau = User.objects.filter(
        deleted_at__isnull=True,
        last_login__gte=day_start,
        last_login__lt=day_end,
    ).count()

    week_start = day_start - timedelta(days=6)
    wau = User.objects.filter(
        deleted_at__isnull=True,
        last_login__gte=week_start,
        last_login__lt=day_end,
    ).count()

    month_start = day_start - timedelta(days=29)
    mau = User.objects.filter(
        deleted_at__isnull=True,
        last_login__gte=month_start,
        last_login__lt=day_end,
    ).count()

    posts_count = Post.objects.filter(
        deleted_at__isnull=True,
        created_at__gte=day_start,
        created_at__lt=day_end,
    ).count()

    comments_count = Comment.objects.filter(
        deleted_at__isnull=True,
        created_at__gte=day_start,
        created_at__lt=day_end,
    ).count()

    reports_received = Report.objects.filter(
        created_at__gte=day_start,
        created_at__lt=day_end,
    ).count()

    reports_resolved = Report.objects.filter(
        reviewed_at__gte=day_start,
        reviewed_at__lt=day_end,
        status__in=[ReportStatus.REVIEWED, ReportStatus.ACTIONED, ReportStatus.DISMISSED],
    ).count()

    # Average resolution time
    resolved_reports = Report.objects.filter(
        reviewed_at__gte=day_start,
        reviewed_at__lt=day_end,
        reviewed_at__isnull=False,
    ).values_list("created_at", "reviewed_at")

    avg_resolution = 0.0
    if resolved_reports.exists():
        deltas = [
            (reviewed - created).total_seconds() / 60 for created, reviewed in resolved_reports
        ]
        avg_resolution = round(sum(deltas) / len(deltas), 1) if deltas else 0.0

    # Upsert — idempotent
    DailyAnalytics.objects.update_or_create(
        date=yesterday,
        defaults={
            "total_users": total_users,
            "new_users": new_users,
            "dau": dau,
            "wau": wau,
            "mau": mau,
            "posts_count": posts_count,
            "comments_count": comments_count,
            "reports_received": reports_received,
            "reports_resolved": reports_resolved,
            "avg_resolution_minutes": avg_resolution,
        },
    )

    logger.info(
        "daily_analytics_calculated",
        extra={"date": str(yesterday), "total_users": total_users},
    )


@shared_task(bind=True, max_retries=1, acks_late=True)
def refresh_dashboard_cache(self):
    """Beat task (every 5 min): pre-warm dashboard cache keys.

    Calls DashboardService methods which populate their Redis caches.
    """
    from core.admin_dashboard.services import DashboardService

    try:
        # Force-refresh by calling the methods (they set cache internally)
        DashboardService.get_metrics()
        DashboardService.get_statistics()
        DashboardService.get_content_health()
        logger.info("dashboard_cache_refreshed")
    except Exception:
        logger.warning("Failed to refresh dashboard cache", exc_info=True)

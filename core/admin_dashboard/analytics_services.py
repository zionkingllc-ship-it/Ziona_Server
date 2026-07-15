"""Admin analytics service — time-series growth/engagement/content charts.

Split from core/admin_dashboard/services.py (no behavior change). Shared cache
helpers live here; services.py re-imports them for DashboardService.
"""

import logging
from datetime import datetime, timedelta, timezone

from django.core.cache import cache

logger = logging.getLogger("core.admin_dashboard")

CACHE_TTL_ANALYTICS = 900  # 15 minutes
CACHE_GENERATED_AT_KEY = "_cache_generated_at"
CACHE_TTL_SECONDS_KEY = "_cache_ttl_seconds"


def _with_cache_metadata(result: dict, ttl_seconds: int) -> dict:
    """Attach cache freshness metadata without changing existing payload fields."""
    return {
        **result,
        CACHE_GENERATED_AT_KEY: datetime.now(timezone.utc).isoformat(),
        CACHE_TTL_SECONDS_KEY: ttl_seconds,
    }


def _calc_percentage_change(old: int | float, new: int | float) -> float:
    """Calculate percentage change between two values."""
    if old == 0:
        return 100.0 if new > 0 else 0.0
    return round((new - old) / old * 100, 1)


class AnalyticsService:
    """Service for time-range filtered analytics charts.

    Reads from pre-aggregated DailyAnalytics table populated by Celery Beat.
    """

    @staticmethod
    def get_user_growth(time_range: str) -> dict:
        """Return user growth chart data for the specified time range.

        Args:
            time_range: 'today' or 'last_month'.

        Returns:
            Dict with labels, data points, and summary stats.
        """
        from core.admin_dashboard.models import DailyAnalytics

        cache_key = f"admin:analytics:user_growth:{(time_range or 'last_month').lower()}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        days = _time_range_to_days(time_range)
        from django.utils import timezone

        start_date = timezone.now().date() - timedelta(days=days - 1)

        entries = (
            DailyAnalytics.objects.filter(date__gte=start_date, date__lte=timezone.now().date())
            .order_by("date")
            .values("date", "total_users", "new_users")
        )

        labels = []
        total_data = []
        new_data = []

        # Fill every calendar day in the range with 0 first, then merge DB
        # entries. This guarantees the arrays are contiguous even when Celery
        # Beat misses a night and no DailyAnalytics row exists for that date.
        filled = _fill_date_gaps(days, entries, ["total_users", "new_users"])
        for day, vals in sorted(filled.items()):
            labels.append(day.strftime("%b %d"))
            total_data.append(vals["total_users"])
            new_data.append(vals["new_users"])

        result = {
            "labels": labels,
            "datasets": [
                {"label": "Total Users", "data": total_data},
                {"label": "New Users", "data": new_data},
            ],
            "summary": {
                "total_users": total_data[-1] if total_data else 0,
                "new_users_period": sum(new_data),
                "growth_rate": _calc_percentage_change(
                    total_data[0] if total_data else 0,
                    total_data[-1] if total_data else 0,
                ),
            },
        }
        result = _with_cache_metadata(result, CACHE_TTL_ANALYTICS)
        cache.set(cache_key, result, CACHE_TTL_ANALYTICS)
        return result

    @staticmethod
    def get_engagement_metrics(time_range: str) -> dict:
        """Return engagement chart data (posts + comments over time)."""
        from core.admin_dashboard.models import DailyAnalytics

        cache_key = f"admin:analytics:engagement:{(time_range or 'last_month').lower()}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        days = _time_range_to_days(time_range)
        from django.utils import timezone

        start_date = timezone.now().date() - timedelta(days=days - 1)

        entries = (
            DailyAnalytics.objects.filter(date__gte=start_date, date__lte=timezone.now().date())
            .order_by("date")
            .values("date", "posts_count", "comments_count")
        )

        labels = []
        posts_data = []
        comments_data = []

        filled = _fill_date_gaps(days, entries, ["posts_count", "comments_count"])
        for day, vals in sorted(filled.items()):
            labels.append(day.strftime("%b %d"))
            posts_data.append(vals["posts_count"])
            comments_data.append(vals["comments_count"])

        result = {
            "labels": labels,
            "datasets": [
                {"label": "Posts", "data": posts_data},
                {"label": "Comments", "data": comments_data},
            ],
            "summary": {
                "total_posts": sum(posts_data),
                "total_comments": sum(comments_data),
            },
        }
        result = _with_cache_metadata(result, CACHE_TTL_ANALYTICS)
        cache.set(cache_key, result, CACHE_TTL_ANALYTICS)
        return result

    @staticmethod
    def get_content_health(time_range: str) -> dict:
        """Return content health chart (reports received vs resolved)."""
        from core.admin_dashboard.models import DailyAnalytics

        cache_key = f"admin:analytics:content_health:{(time_range or 'last_month').lower()}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        days = _time_range_to_days(time_range)
        from django.utils import timezone

        start_date = timezone.now().date() - timedelta(days=days - 1)

        entries = (
            DailyAnalytics.objects.filter(date__gte=start_date, date__lte=timezone.now().date())
            .order_by("date")
            .values("date", "reports_received", "reports_resolved", "avg_resolution_minutes")
        )

        labels = []
        received_data = []
        resolved_data = []

        filled = _fill_date_gaps(days, entries, ["reports_received", "reports_resolved"])
        for day, vals in sorted(filled.items()):
            labels.append(day.strftime("%b %d"))
            received_data.append(vals["reports_received"])
            resolved_data.append(vals["reports_resolved"])

        result = {
            "labels": labels,
            "datasets": [
                {"label": "Reports Received", "data": received_data},
                {"label": "Reports Resolved", "data": resolved_data},
            ],
            "summary": {
                "total_received": sum(received_data),
                "total_resolved": sum(resolved_data),
                "resolution_rate": (
                    round(sum(resolved_data) / sum(received_data) * 100, 1)
                    if sum(received_data) > 0
                    else 0.0
                ),
            },
        }
        result = _with_cache_metadata(result, CACHE_TTL_ANALYTICS)
        cache.set(cache_key, result, CACHE_TTL_ANALYTICS)
        return result


def _time_range_to_days(time_range: str) -> int:
    """Convert time range string to number of days."""
    time_range = (time_range or "").lower()
    mapping = {
        "today": 1,
        "last_week": 7,
        "last_month": 30,
        "last_quarter": 90,
    }
    return mapping.get(time_range, 30)


def _daily_analytics_snapshot(day) -> dict:
    """Compute a single day's analytics directly from source tables."""
    from core.engagement.models import Comment
    from core.moderation.models import Report, ReportStatus
    from core.posts.models import Post
    from core.users.models import User

    day_start = datetime.combine(day, datetime.min.time()).replace(tzinfo=timezone.utc)
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

    week_start = day_start - timedelta(days=6)
    month_start = day_start - timedelta(days=29)

    dau = User.objects.filter(
        deleted_at__isnull=True,
        last_login__gte=day_start,
        last_login__lt=day_end,
    ).count()
    wau = User.objects.filter(
        deleted_at__isnull=True,
        last_login__gte=week_start,
        last_login__lt=day_end,
    ).count()
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

    return {
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
    }


def _fill_date_gaps(days: int, db_entries, date_fields: list[str]) -> dict:
    """Build a complete date-keyed baseline for the given window, padded with zeros.

    Iterates the full date range in Python and merges DB entries into it.
    This guarantees chart arrays are contiguous (no skipped X-axis points)
    even when Celery Beat misses a night and no DailyAnalytics row exists.

    Args:
        days: Number of past days to cover.
        db_entries: Queryset or iterable of dicts with a 'date' key.
        date_fields: Field names to extract from each entry.

    Returns:
        OrderedDict[date, {field: value}] covering all `days` days.
    """
    from django.utils import timezone

    baseline: dict = {}
    today = timezone.now().date()

    for i in range(days - 1, -1, -1):
        day = today - timedelta(days=i)
        baseline[day] = dict.fromkeys(date_fields, 0)

    entries = list(db_entries)
    db_days = {entry["date"] for entry in entries}
    fallback_days = set(baseline) - db_days
    if today in baseline:
        fallback_days.add(today)

    # Fill source-backed gaps with grouped aggregate queries. This keeps charts
    # useful when DailyAnalytics rows are missing without recomputing each day
    # individually.
    source_values = _bulk_daily_analytics_snapshots(fallback_days, date_fields)
    for day, vals in source_values.items():
        if day in baseline:
            for field in date_fields:
                baseline[day][field] = vals.get(field, baseline[day][field])

    # Merge real DB values where rows exist
    for entry in entries:
        day = entry["date"]
        if day in baseline:
            for field in date_fields:
                baseline[day][field] = entry.get(field, 0)

    if today in source_values:
        for field in date_fields:
            baseline[today][field] = source_values[today].get(field, baseline[today][field])

    return baseline


def _bulk_daily_analytics_snapshots(days: set, date_fields: list[str]) -> dict:
    """Compute missing analytics days with grouped source-table aggregates."""
    if not days:
        return {}

    from django.db.models import Count
    from django.db.models.functions import TruncDate

    from core.engagement.models import Comment
    from core.moderation.models import Report, ReportStatus
    from core.posts.models import Post
    from core.users.models import User

    ordered_days = sorted(days)
    start_date = ordered_days[0]
    end_date = ordered_days[-1]
    start_dt = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=timezone.utc)
    end_dt = datetime.combine(end_date + timedelta(days=1), datetime.min.time()).replace(
        tzinfo=timezone.utc
    )

    result = {day: dict.fromkeys(date_fields, 0) for day in ordered_days}

    def grouped_counts(model, date_field: str, **filters) -> dict:
        rows = (
            model.objects.filter(
                **{
                    f"{date_field}__gte": start_dt,
                    f"{date_field}__lt": end_dt,
                    **filters,
                }
            )
            .annotate(day=TruncDate(date_field))
            .values("day")
            .annotate(count=Count("id"))
        )
        return {row["day"]: row["count"] for row in rows if row["day"] in result}

    new_users_by_day: dict = {}
    if "new_users" in date_fields or "total_users" in date_fields:
        new_users_by_day = grouped_counts(User, "created_at", deleted_at__isnull=True)

    if "new_users" in date_fields:
        for day, count in new_users_by_day.items():
            result[day]["new_users"] = count

    if "total_users" in date_fields:
        running_total = User.objects.filter(
            deleted_at__isnull=True,
            created_at__lt=start_dt,
        ).count()
        for day in ordered_days:
            running_total += new_users_by_day.get(day, 0)
            result[day]["total_users"] = running_total

    if "posts_count" in date_fields:
        for day, count in grouped_counts(Post, "created_at", deleted_at__isnull=True).items():
            result[day]["posts_count"] = count

    if "comments_count" in date_fields:
        for day, count in grouped_counts(Comment, "created_at", deleted_at__isnull=True).items():
            result[day]["comments_count"] = count

    if "reports_received" in date_fields:
        for day, count in grouped_counts(Report, "created_at").items():
            result[day]["reports_received"] = count

    if "reports_resolved" in date_fields:
        for day, count in grouped_counts(
            Report,
            "reviewed_at",
            reviewed_at__isnull=False,
            status__in=[ReportStatus.REVIEWED, ReportStatus.ACTIONED, ReportStatus.DISMISSED],
        ).items():
            result[day]["reports_resolved"] = count

    return result

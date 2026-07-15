"""
Dashboard & Analytics services — metrics, statistics, and chart data.

All read methods use Redis caching. Analytics reads from pre-aggregated DailyAnalytics.
"""

import logging
from datetime import datetime, timedelta, timezone

from django.core.cache import cache

logger = logging.getLogger("core.admin_dashboard")

# Cache keys
CACHE_DASHBOARD_METRICS = "admin:dashboard:metrics"
CACHE_DASHBOARD_STATS = "admin:dashboard:stats"
CACHE_DASHBOARD_HEALTH = "admin:dashboard:health"
CACHE_TTL_SHORT = 300  # 5 minutes
CACHE_TTL_ANALYTICS = 900  # 15 minutes
CACHE_GENERATED_AT_KEY = "_cache_generated_at"
CACHE_TTL_SECONDS_KEY = "_cache_ttl_seconds"

from core.admin_dashboard.analytics_services import (  # noqa: E402,F401
    AnalyticsService,
    _calc_percentage_change,
    _with_cache_metadata,
)


class DashboardService:
    """Service for dashboard overview cards, stats, and recent activities."""

    @staticmethod
    def get_metrics() -> dict:
        """Return 4 top-level metric cards with percentage change vs yesterday.

        Cards: Total Users, Posts Today, Pending Reports, Avg Engagement Rate.
        """
        cached = cache.get(CACHE_DASHBOARD_METRICS)
        if cached:
            return cached

        from core.engagement.models import Comment, Like
        from core.moderation.models import Report, ReportStatus
        from core.posts.models import Post
        from core.users.models import User

        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        yesterday_start = today_start - timedelta(days=1)

        # Total Users
        total_users = User.objects.filter(deleted_at__isnull=True).count()
        users_yesterday = User.objects.filter(
            deleted_at__isnull=True, created_at__lt=today_start
        ).count()
        users_change = _calc_percentage_change(users_yesterday, total_users)

        # Posts Today
        posts_today = Post.objects.filter(
            deleted_at__isnull=True, created_at__gte=today_start
        ).count()
        posts_yesterday = Post.objects.filter(
            deleted_at__isnull=True,
            created_at__gte=yesterday_start,
            created_at__lt=today_start,
        ).count()
        posts_change = _calc_percentage_change(posts_yesterday, posts_today)

        # Pending Reports
        pending_reports = Report.objects.filter(status=ReportStatus.PENDING).count()

        # Avg Engagement (likes + comments today / posts today)
        likes_today = Like.objects.filter(created_at__gte=today_start).count()
        comments_today = Comment.objects.filter(
            deleted_at__isnull=True, created_at__gte=today_start
        ).count()
        engagement_today = likes_today + comments_today
        likes_yesterday = Like.objects.filter(
            created_at__gte=yesterday_start, created_at__lt=today_start
        ).count()
        comments_yesterday = Comment.objects.filter(
            deleted_at__isnull=True,
            created_at__gte=yesterday_start,
            created_at__lt=today_start,
        ).count()
        engagement_yesterday = likes_yesterday + comments_yesterday
        engagement_change = _calc_percentage_change(engagement_yesterday, engagement_today)

        result = {
            "total_users": {
                "value": total_users,
                "change": users_change,
                "label": "Total Users",
            },
            "posts_today": {
                "value": posts_today,
                "change": posts_change,
                "label": "Posts Today",
            },
            "pending_reports": {
                "value": pending_reports,
                "change": 0.0,
                "label": "Pending Reports",
            },
            "engagement": {
                "value": engagement_today,
                "change": engagement_change,
                "label": "Engagement Today",
            },
        }

        result = _with_cache_metadata(result, CACHE_TTL_SHORT)
        cache.set(CACHE_DASHBOARD_METRICS, result, CACHE_TTL_SHORT)
        return result

    @staticmethod
    def get_statistics() -> dict:
        """Return DAU, WAU, MAU, and avg report resolution time."""
        cached = cache.get(CACHE_DASHBOARD_STATS)
        if cached:
            return cached

        from django.db.models import Avg, DurationField, ExpressionWrapper, F

        from core.moderation.models import Report, ReportStatus
        from core.users.models import User

        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        # DAU — users with any activity today (approximated by last_login)
        dau = User.objects.filter(
            deleted_at__isnull=True,
            last_login__gte=today_start,
        ).count()

        # WAU
        week_ago = today_start - timedelta(days=7)
        wau = User.objects.filter(
            deleted_at__isnull=True,
            last_login__gte=week_ago,
        ).count()

        # MAU
        month_ago = today_start - timedelta(days=30)
        mau = User.objects.filter(
            deleted_at__isnull=True,
            last_login__gte=month_ago,
        ).count()

        # Avg resolution time for reports resolved in last 30 days.
        # Keep this in the database so the dashboard card stays O(1) as reports grow.
        avg_duration = Report.objects.filter(
            status__in=[ReportStatus.REVIEWED, ReportStatus.ACTIONED],
            reviewed_at__isnull=False,
            reviewed_at__gte=month_ago,
        ).aggregate(
            avg_duration=Avg(
                ExpressionWrapper(
                    F("reviewed_at") - F("created_at"),
                    output_field=DurationField(),
                )
            )
        )["avg_duration"]
        avg_resolution = round(avg_duration.total_seconds() / 60, 1) if avg_duration else 0.0

        result = {
            "dau": dau,
            "wau": wau,
            "mau": mau,
            "avg_resolution_minutes": avg_resolution,
        }

        result = _with_cache_metadata(result, CACHE_TTL_SHORT)
        cache.set(CACHE_DASHBOARD_STATS, result, CACHE_TTL_SHORT)
        return result

    @staticmethod
    def get_recent_activities(limit: int = 15) -> list[dict]:
        """Return recent admin actions and platform events, grouped by day.

        Pulls from AdminAuditLog and aggregates into a timeline.
        """
        from core.admin_dashboard.models import AdminAuditLog

        entries = AdminAuditLog.objects.select_related("admin_user").order_by("-created_at")[:limit]

        activities = []
        for entry in entries:
            admin_name = ""
            if entry.admin_user:
                admin_name = entry.admin_user.full_name or entry.admin_user.username

            activities.append(
                {
                    "id": str(entry.id),
                    "action": entry.action,
                    "description": _format_action_description(entry),
                    "admin_name": admin_name,
                    "target_type": entry.target_type,
                    "target_id": entry.target_id,
                    "created_at": entry.created_at.isoformat(),
                }
            )

        return activities

    @staticmethod
    def get_content_health() -> list[dict]:
        """Return content distribution breakdown for the health chart."""
        cached = cache.get(CACHE_DASHBOARD_HEALTH)
        if cached:
            return cached

        from core.circles.models import AnchorResponse
        from core.posts.models import Post

        total_posts = Post.objects.filter(deleted_at__isnull=True).count()
        total_responses = AnchorResponse.objects.filter(deleted_at__isnull=True).count()
        total = total_posts + total_responses or 1

        result = [
            {
                "label": "User Posts",
                "value": total_posts,
                "percentage": round(total_posts / total * 100, 1),
                "color": "#6366F1",
            },
            {
                "label": "Circle Responses",
                "value": total_responses,
                "percentage": round(total_responses / total * 100, 1),
                "color": "#8B5CF6",
            },
        ]

        cache.set(CACHE_DASHBOARD_HEALTH, result, CACHE_TTL_SHORT)
        return result


# ─────────────────────────────────────────
# Private helpers
# ─────────────────────────────────────────


def _format_action_description(audit_entry) -> str:
    """Generate a human-readable description for an audit log entry."""
    action_map = {
        "USER_WARNED": "warned a user",
        "USER_SUSPENDED": "suspended a user",
        "USER_DELETED": "deleted a user",
        "USER_REACTIVATED": "reactivated a user",
        "CIRCLE_CREATED": "created a new circle",
        "CIRCLE_EDITED": "edited a circle",
        "CIRCLE_ACTIVATED": "activated a circle",
        "CIRCLE_DEACTIVATED": "deactivated a circle",
        "ANCHOR_CREATED": "created a new anchor",
        "ANCHOR_SCHEDULED": "scheduled an anchor",
        "ANCHOR_POSTED": "posted an anchor",
        "ANCHOR_CANCELLED": "cancelled a scheduled anchor",
        "REPORT_REVIEWED": "reviewed a report",
        "CONTACT_REPLIED": "replied to a contact message",
        "ADMIN_LOGIN": "logged in",
        "UNAUTHORIZED_ACCESS_ATTEMPT": "unauthorized access attempt detected",
    }
    admin_name = ""
    if audit_entry.admin_user:
        admin_name = audit_entry.admin_user.full_name or audit_entry.admin_user.username

    action_text = action_map.get(audit_entry.action, audit_entry.action.lower().replace("_", " "))
    return f"{admin_name} {action_text}".strip()

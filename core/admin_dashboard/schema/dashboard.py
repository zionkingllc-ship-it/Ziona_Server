"""Admin dashboard metrics + analytics charts.

Split from the former core/admin_dashboard/schema.py (no contract change).
"""

from __future__ import annotations

from enum import Enum

import strawberry
from strawberry.types import Info

from core.admin_dashboard.permissions import admin_required


@strawberry.enum
class AnalyticsTimeRange(Enum):
    TODAY = "today"
    LAST_WEEK = "last_week"
    LAST_MONTH = "last_month"
    LAST_QUARTER = "last_quarter"


@strawberry.type
class MetricCardType:
    """A single dashboard metric card."""

    label: str
    value: int
    change: float


@strawberry.type
class StatisticsType:
    """Platform-wide statistics."""

    dau: int
    wau: int
    mau: int
    avg_resolution_minutes: float = strawberry.field(name="avgResolutionMinutes")


@strawberry.type
class ActivityType:
    """A single recent activity entry."""

    id: str
    action: str
    description: str
    admin_name: str = strawberry.field(name="adminName")
    target_type: str = strawberry.field(name="targetType")
    target_id: str = strawberry.field(name="targetId")
    created_at: str = strawberry.field(name="createdAt")


@strawberry.type
class ContentHealthItemType:
    """Content distribution item."""

    label: str
    value: int
    percentage: float
    color: str


@strawberry.type
class AdminDashboardType:
    """Full dashboard overview response."""

    total_users: MetricCardType = strawberry.field(name="totalUsers")
    posts_today: MetricCardType = strawberry.field(name="postsToday")
    pending_reports: MetricCardType = strawberry.field(name="pendingReports")
    engagement: MetricCardType = strawberry.field(name="engagement")
    statistics: StatisticsType
    content_health: list[ContentHealthItemType] = strawberry.field(name="contentHealth")
    last_updated: str = strawberry.field(name="lastUpdated")
    cache_ttl_seconds: int = strawberry.field(name="cacheTtlSeconds")


@strawberry.type
class DatasetType:
    """A single dataset in a chart."""

    label: str
    data: list[int]


@strawberry.type
class ChartSummaryType:
    """Summary stats for a chart."""

    data: strawberry.scalars.JSON


@strawberry.type
class ChartDataType:
    """Chart data with labels, datasets, and summary."""

    labels: list[str]
    datasets: list[DatasetType]
    summary: strawberry.scalars.JSON


@strawberry.type
class AdminAnalyticsType:
    """Full analytics response."""

    user_growth: ChartDataType = strawberry.field(name="userGrowth")
    engagement_metrics: ChartDataType = strawberry.field(name="engagementMetrics")
    content_health: ChartDataType = strawberry.field(name="contentHealth")
    last_updated: str = strawberry.field(name="lastUpdated")
    cache_ttl_seconds: int = strawberry.field(name="cacheTtlSeconds")


def _cache_generated_at(*payloads: dict) -> str:
    from datetime import datetime, timezone

    for payload in payloads:
        value = payload.get("_cache_generated_at") if isinstance(payload, dict) else None
        if value:
            return str(value)
    return datetime.now(timezone.utc).isoformat()


def _cache_ttl_seconds(*payloads: dict, default: int) -> int:
    for payload in payloads:
        value = payload.get("_cache_ttl_seconds") if isinstance(payload, dict) else None
        if value is not None:
            return int(value)
    return default


def _to_chart_data(data: dict) -> ChartDataType:
    """Convert service dict to ChartDataType."""
    return ChartDataType(
        labels=data.get("labels", []),
        datasets=[DatasetType(**d) for d in data.get("datasets", [])],
        summary=data.get("summary", {}),
    )


@strawberry.type
class DashboardAdminQueries:
    @strawberry.field(name="adminDashboard", description="Get dashboard overview metrics.")
    @admin_required
    def admin_dashboard(self, info: Info) -> AdminDashboardType:
        from core.admin_dashboard.services import DashboardService

        metrics = DashboardService.get_metrics()
        stats = DashboardService.get_statistics()
        health = DashboardService.get_content_health()

        return AdminDashboardType(
            total_users=MetricCardType(**metrics["total_users"]),
            posts_today=MetricCardType(**metrics["posts_today"]),
            pending_reports=MetricCardType(**metrics["pending_reports"]),
            engagement=MetricCardType(**metrics["engagement"]),
            statistics=StatisticsType(
                dau=stats["dau"],
                wau=stats["wau"],
                mau=stats["mau"],
                avg_resolution_minutes=stats["avg_resolution_minutes"],
            ),
            content_health=[ContentHealthItemType(**item) for item in health],
            last_updated=_cache_generated_at(metrics, stats),
            cache_ttl_seconds=_cache_ttl_seconds(metrics, stats, default=300),
        )

    @strawberry.field(
        name="adminRecentActivities",
        description="Get recent admin activities timeline.",
    )
    @admin_required
    def admin_recent_activities(self, info: Info, limit: int = 15) -> list[ActivityType]:
        from core.admin_dashboard.services import DashboardService

        activities = DashboardService.get_recent_activities(limit=limit)
        return [ActivityType(**a) for a in activities]

    @strawberry.field(
        name="adminAnalytics",
        description="Get analytics charts for a time range.",
    )
    @admin_required
    def admin_analytics(self, info: Info, time_range: str = "last_month") -> AdminAnalyticsType:
        from core.admin_dashboard.services import AnalyticsService

        growth = AnalyticsService.get_user_growth(time_range)
        engagement = AnalyticsService.get_engagement_metrics(time_range)
        health = AnalyticsService.get_content_health(time_range)

        return AdminAnalyticsType(
            user_growth=_to_chart_data(growth),
            engagement_metrics=_to_chart_data(engagement),
            content_health=_to_chart_data(health),
            last_updated=_cache_generated_at(growth, engagement, health),
            cache_ttl_seconds=_cache_ttl_seconds(growth, engagement, health, default=900),
        )

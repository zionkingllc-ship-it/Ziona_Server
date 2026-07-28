from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.utils import timezone

from core.admin_dashboard.models import DailyAnalytics
from core.admin_dashboard.services import AnalyticsService, DashboardService
from core.authentication.services import AuthService
from core.engagement.models import Comment
from core.moderation.models import Report, ReportReason, ReportStatus
from core.posts.models import Post
from core.users.models import User


@pytest.mark.django_db
def test_calculate_daily_analytics(authenticated_admin):
    # This just ensures we can import and run basic schema interactions
    date = timezone.now().date() - timedelta(days=1)
    DailyAnalytics.objects.create(
        date=date,
        total_users=10,
        new_users=2,
        dau=5,
        wau=8,
        mau=10,
        posts_count=20,
        comments_count=15,
        reports_received=1,
        reports_resolved=0,
        avg_resolution_minutes=0.0,
    )

    data = AnalyticsService.get_user_growth("LAST_MONTH")
    assert isinstance(data, dict)


@pytest.mark.django_db
def test_login_updates_last_login_for_dashboard_activity():
    user = User.objects.create_user(
        email="login-activity@example.com",
        username="loginactivity",
        password="SecurePass1!",
        is_email_verified=True,
    )
    assert user.last_login is None

    AuthService.login("login-activity@example.com", "SecurePass1!", ip_address="203.0.113.20")

    user.refresh_from_db()
    assert user.last_login is not None
    assert str(user.last_login_ip) == "203.0.113.20"


@pytest.mark.django_db
def test_dashboard_statistics_uses_recent_auth_activity():
    cache.clear()
    user = User.objects.create_user(
        email="stats-activity@example.com",
        username="statsactivity",
        password="SecurePass1!",
        is_email_verified=True,
    )
    user.last_login = timezone.now()
    user.save(update_fields=["last_login", "updated_at"])

    stats = DashboardService.get_statistics()

    assert stats["dau"] >= 1
    assert stats["wau"] >= 1
    assert stats["mau"] >= 1


@pytest.mark.django_db
def test_dashboard_statistics_aggregates_report_resolution_time():
    cache.clear()
    user = User.objects.create_user(
        email="report-resolution@example.com",
        username="reportresolution",
        password="SecurePass1!",
        is_email_verified=True,
    )
    report = Report.objects.create(
        reporter=user,
        reason=ReportReason.OTHER,
        description="Needs review",
    )
    created_at = timezone.now() - timedelta(hours=2)
    reviewed_at = created_at + timedelta(minutes=45)
    Report.objects.filter(id=report.id).update(
        status=ReportStatus.REVIEWED,
        created_at=created_at,
        reviewed_at=reviewed_at,
    )

    stats = DashboardService.get_statistics()

    assert stats["avg_resolution_minutes"] == 45.0


@pytest.mark.django_db
def test_analytics_live_fallback_returns_current_source_data():
    user = User.objects.create_user(
        email="analytics-source@example.com",
        username="analyticssource",
        password="SecurePass1!",
        is_email_verified=True,
    )
    post = Post.objects.create(user=user, post_type="text", caption="Live analytics")
    Comment.objects.create(post=post, user=user, text="Live analytics comment")

    growth = AnalyticsService.get_user_growth("today")
    engagement = AnalyticsService.get_engagement_metrics("today")

    assert growth["summary"]["total_users"] >= 1
    assert engagement["summary"]["total_posts"] >= 1
    assert engagement["summary"]["total_comments"] >= 1


@pytest.mark.django_db
def test_user_growth_includes_day_over_day_new_user_comparison():
    cache.clear()
    today = timezone.now().date()

    # Yesterday comes from the pre-aggregated DailyAnalytics row...
    DailyAnalytics.objects.create(
        date=today - timedelta(days=1),
        total_users=105,
        new_users=1,
        dau=0,
        wau=0,
        mau=0,
        posts_count=0,
        comments_count=0,
        reports_received=0,
        reports_resolved=0,
        avg_resolution_minutes=0.0,
    )
    # ...today is always computed live from the User table (2 new users today).
    for i in range(2):
        User.objects.create_user(
            email=f"grow{i}@example.com", username=f"grow{i}", password="SecurePass1!"
        )

    summary = AnalyticsService.get_user_growth("last_month")["summary"]

    assert summary["new_users_today"] == 2
    assert summary["new_users_yesterday"] == 1
    assert summary["daily_growth_rate"] == 100.0  # (2 - 1) / 1 * 100


@pytest.mark.django_db
def test_dashboard_engagement_rate_is_share_of_active_users():
    cache.clear()
    now = timezone.now()
    u1 = User.objects.create_user(
        email="eng1@example.com", username="eng1", password="SecurePass1!"
    )
    u2 = User.objects.create_user(
        email="eng2@example.com", username="eng2", password="SecurePass1!"
    )
    # Both active today; only u1 engages.
    User.objects.filter(id__in=[u1.id, u2.id]).update(last_login=now)
    post = Post.objects.create(user=u1, post_type="text", caption="hello")
    Comment.objects.create(post=post, user=u1, text="engaged")

    metrics = DashboardService.get_metrics()

    # Raw count field is unchanged; the additive rate is 1 engaged / 2 active = 50%.
    assert metrics["engagement"]["value"] == 1
    assert metrics["engagement"]["rate"] == 50.0


@pytest.mark.django_db
def test_dashboard_engagement_rate_handles_zero_active_users():
    cache.clear()
    metrics = DashboardService.get_metrics()
    assert metrics["engagement"]["rate"] == 0.0  # no divide-by-zero


@pytest.mark.django_db
def test_last_quarter_user_growth_does_not_recompute_every_day():
    cache.clear()
    today = timezone.now().date()
    historical_date = today - timedelta(days=45)
    DailyAnalytics.objects.create(
        date=historical_date,
        total_users=25,
        new_users=3,
        dau=8,
        wau=12,
        mau=20,
        posts_count=4,
        comments_count=5,
        reports_received=0,
        reports_resolved=0,
        avg_resolution_minutes=0.0,
    )

    with patch(
        "core.admin_dashboard.analytics_services._daily_analytics_snapshot",
        return_value={"total_users": 30, "new_users": 1},
    ) as snapshot:
        growth = AnalyticsService.get_user_growth("last_quarter")

    assert snapshot.call_count == 0
    assert len(growth["labels"]) == 90
    assert 25 in growth["datasets"][0]["data"]

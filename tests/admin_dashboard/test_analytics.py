from datetime import timedelta

import pytest
from django.core.cache import cache
from django.utils import timezone

from core.admin_dashboard.models import DailyAnalytics
from core.admin_dashboard.services import AnalyticsService, DashboardService
from core.authentication.services import AuthService
from core.engagement.models import Comment
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

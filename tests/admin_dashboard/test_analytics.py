from datetime import timedelta

import pytest
from django.utils import timezone

from core.admin_dashboard.models import DailyAnalytics
from core.admin_dashboard.services import AnalyticsService


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

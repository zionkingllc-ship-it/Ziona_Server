"""Daily activity history: recorded per authenticated request, read by DAU/WAU/MAU."""

import json
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import DatabaseError, connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.admin_dashboard.models import DailyAnalytics
from core.admin_dashboard.tasks import calculate_daily_analytics
from core.authentication import activity
from core.authentication.activity import active_users_between, record_daily_activity
from core.authentication.services import AuthService
from core.users.models import User, UserDailyActivity

QUERY = json.dumps({"query": "query { suggestedCreators(limit: 1) { id } }"})


@pytest.fixture(autouse=True)
def _fresh_process_memory():
    activity._recorded_today.clear()
    yield
    activity._recorded_today.clear()


def _graphql(token: str | None = None):
    client = Client()
    if token:
        client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    return client.post("/graphql/", data=QUERY, content_type="application/json")


def _midnight(days_ago: int = 0):
    now = timezone.now()
    return now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days_ago)


def _user(username: str, **fields) -> User:
    return User.objects.create_user(
        email=f"{username}@example.com",
        username=username,
        is_email_verified=True,
        **fields,
    )


# ── Recording ────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_authenticated_request_records_today_without_a_login(authenticated_user):
    """The gap this closes: a still-valid token never touches last_login."""
    user = authenticated_user["user"]
    assert user.last_login is None

    response = _graphql(authenticated_user["access_token"])

    assert response.status_code == 200
    rows = UserDailyActivity.objects.filter(user=user)
    assert list(rows.values_list("date", flat=True)) == [timezone.now().date()]
    user.refresh_from_db()
    assert user.last_login is None  # recorded without pretending to be a login


@pytest.mark.django_db
def test_later_requests_the_same_day_skip_the_database(authenticated_user):
    _graphql(authenticated_user["access_token"])

    with CaptureQueriesContext(connection) as ctx:
        _graphql(authenticated_user["access_token"])

    assert not [q for q in ctx.captured_queries if "user_daily_activity" in q["sql"]]
    assert UserDailyActivity.objects.count() == 1


@pytest.mark.django_db
def test_next_day_records_a_second_row(authenticated_user):
    user_id = authenticated_user["user"].id
    tomorrow = timezone.now() + timedelta(days=1)

    record_daily_activity(user_id)
    with patch("core.authentication.activity.timezone.now", return_value=tomorrow):
        record_daily_activity(user_id)

    assert UserDailyActivity.objects.filter(user_id=user_id).count() == 2


@pytest.mark.django_db
def test_row_written_by_another_process_does_not_raise(authenticated_user):
    user_id = authenticated_user["user"].id
    record_daily_activity(user_id)
    activity._recorded_today.clear()  # as if a different worker process

    record_daily_activity(user_id)

    assert UserDailyActivity.objects.filter(user_id=user_id).count() == 1


@pytest.mark.django_db
def test_database_failure_does_not_fail_the_request(authenticated_user):
    with patch.object(UserDailyActivity.objects, "bulk_create", side_effect=DatabaseError("down")):
        response = _graphql(authenticated_user["access_token"])

    assert response.status_code == 200
    assert "errors" not in response.json()
    # Not remembered, so the next request retries instead of losing the day.
    assert str(authenticated_user["user"].id) not in activity._recorded_today


@pytest.mark.django_db
@pytest.mark.parametrize("token", [None, "not-a-jwt"])
def test_guests_and_invalid_tokens_record_nothing(token):
    _graphql(token)

    assert UserDailyActivity.objects.count() == 0


@pytest.mark.django_db
def test_login_records_activity():
    user = _user("loginday", password="SecurePass1!")  # pragma: allowlist secret

    AuthService.login("loginday@example.com", "SecurePass1!")  # pragma: allowlist secret

    assert UserDailyActivity.objects.filter(user=user, date=timezone.now().date()).exists()


# ── Reading ──────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_active_today_counts_toward_dau_even_when_last_login_is_old():
    """The regression this fixes — last_login alone would report 0."""
    user = _user("oldlogin", last_login=_midnight(3))
    UserDailyActivity.objects.create(user=user, date=timezone.now().date())

    assert list(active_users_between(_midnight(0))) == [user]


@pytest.mark.django_db
def test_last_login_alone_still_counts_for_days_before_activity_existed():
    user = _user("legacy", last_login=_midnight(1) + timedelta(hours=9))

    assert list(active_users_between(_midnight(1), _midnight(0))) == [user]


@pytest.mark.django_db
def test_user_with_both_signals_is_counted_once():
    user = _user("both", last_login=timezone.now())
    UserDailyActivity.objects.create(user=user, date=timezone.now().date())

    assert active_users_between(_midnight(0)).count() == 1


@pytest.mark.django_db
def test_window_end_is_exclusive_and_deleted_users_are_excluded():
    kept = _user("kept")
    deleted = _user("gone", deleted_at=timezone.now())
    today = timezone.now().date()
    UserDailyActivity.objects.create(user=kept, date=today - timedelta(days=1))
    UserDailyActivity.objects.create(user=kept, date=today)  # outside [yesterday, today)
    UserDailyActivity.objects.create(user=deleted, date=today - timedelta(days=1))

    assert list(active_users_between(_midnight(1), _midnight(0))) == [kept]


@pytest.mark.django_db
def test_nightly_snapshot_counts_activity_rows():
    yesterday = timezone.now().date() - timedelta(days=1)
    user = _user("snapshot", last_login=_midnight(10))
    UserDailyActivity.objects.create(user=user, date=yesterday)

    calculate_daily_analytics()

    row = DailyAnalytics.objects.get(date=yesterday)
    assert (row.dau, row.wau, row.mau) == (1, 1, 1)

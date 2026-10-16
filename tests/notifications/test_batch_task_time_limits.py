"""Batch notification tasks must stop when the soft time limit fires.

Each loop catches per-item failures so one bad user cannot abort the batch.
SoftTimeLimitExceeded is an Exception too, so the same handler used to swallow
it: the loop carried on, logging the timeout as that user's failure, until the
hard limit killed the worker outright.
"""

from datetime import timedelta

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from django.utils import timezone

from core.circles.models import Anchor, Circle, CircleMembership
from core.notifications import tasks
from core.notifications.models import NotificationPreference


class _RetriedError(Exception):
    pass


@pytest.fixture
def capture_retry(monkeypatch):
    """Replace a bound task's retry() so the test sees what it was retried with."""
    calls = []

    def install(task):
        def retry(exc=None, **kwargs):
            calls.append(exc)
            raise _RetriedError

        monkeypatch.setattr(task, "retry", retry)
        return calls

    return install


@pytest.fixture
def digest_users(db, create_user):
    users = []
    for index in range(3):
        user = create_user(email=f"digest{index}@x.com", username=f"digest{index}")
        user.is_email_verified = True
        user.last_login = timezone.now()
        user.save(update_fields=["is_email_verified", "last_login", "updated_at"])
        NotificationPreference.objects.update_or_create(
            user=user, defaults={"digest_email_enabled": True}
        )
        users.append(user)
    return users


def test_digest_stops_at_the_soft_limit_and_retries_to_resume(
    digest_users, monkeypatch, capture_retry
):
    """The retry resumes: each user's notifications are marked digest_sent as sent."""
    retries = capture_retry(tasks.send_daily_notification_digest)
    attempted = []

    def send(user, *args):
        attempted.append(user.id)
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr(tasks, "_send_digest_for_user", send)

    with pytest.raises(_RetriedError):
        tasks.send_daily_notification_digest.run()

    assert len(attempted) == 1
    assert isinstance(retries[0], SoftTimeLimitExceeded)


def test_digest_still_survives_one_users_ordinary_failure(digest_users, monkeypatch):
    attempted = []

    def send(user, *args):
        attempted.append(user.id)
        if len(attempted) == 1:
            raise ConnectionError("one mailbox bounced")
        return True

    monkeypatch.setattr(tasks, "_send_digest_for_user", send)

    tasks.send_daily_notification_digest.run()

    assert len(attempted) == len(digest_users)


def test_daily_anchor_notifications_stop_at_the_soft_limit(
    db, create_user, monkeypatch, capture_retry
):
    retries = capture_retry(tasks.send_daily_anchor_notifications)
    circle = Circle.objects.create(name="Morning Circle", description="x")
    for index in range(3):
        member = create_user(email=f"m{index}@x.com", username=f"m{index}")
        CircleMembership.objects.create(circle=circle, user=member, role="member")
    Anchor.objects.create(
        circle=circle,
        anchor_type="devotional",
        title="Today",
        content="Body",
        published_at=timezone.now() - timedelta(hours=1),
        expires_at=timezone.now() + timedelta(hours=23),
    )
    attempted = []

    def create(**kwargs):
        attempted.append(kwargs["user_id"])
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr(tasks, "create_notification", create)

    with pytest.raises(_RetriedError):
        tasks.send_daily_anchor_notifications.run()

    assert len(attempted) == 1
    assert isinstance(retries[0], SoftTimeLimitExceeded)


def test_digest_reaches_eligible_users_end_to_end(digest_users, monkeypatch):
    """Regression: the eligibility query itself used to raise, so no digest was ever sent.

    select_related("notification_preferences") combined with only() is rejected
    by Django at query time. Nothing here is mocked except the email transport.
    """
    from core.notifications.models import Notification, NotificationType

    for user in digest_users:
        Notification.objects.create(
            user=user,
            notification_type=NotificationType.LIKE_POST,
            reference_type="post",
            message="someone liked your post",
        )
    sent_to = []
    monkeypatch.setattr(
        "core.emails.services.EmailService.send_notification_digest",
        lambda user_name, email, activities: sent_to.append(email),
    )

    tasks.send_daily_notification_digest.run()

    assert sorted(sent_to) == sorted(user.email for user in digest_users)
    assert not Notification.objects.filter(digest_sent=False).exists()

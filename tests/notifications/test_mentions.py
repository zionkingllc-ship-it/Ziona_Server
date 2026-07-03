"""Tests for the centralized @mention notification pipeline.

Covers the parsing helper and notify_mentions, including the circle-scoped
membership guard that keeps private-circle activity from leaking to non-members.
"""

import uuid

import pytest
from django.contrib.auth import get_user_model

from core.notifications.models import Notification, NotificationType
from core.notifications.services import extract_mentioned_usernames, notify_mentions

User = get_user_model()


def _make_user(username: str) -> "User":
    return User.objects.create_user(
        email=f"{username}@example.com",
        username=username,
        password="password123",
    )


# ── extract_mentioned_usernames (pure) ─────────────────────────────────


def test_extract_dedups_case_insensitively_and_preserves_order():
    assert extract_mentioned_usernames("hey @alice and @Bob, then @alice again") == [
        "alice",
        "Bob",
    ]


def test_extract_ignores_emails_and_too_short_handles():
    # "bob@example.com" — the @ is preceded by a word char, so not a mention.
    # "@ab" — below the 3-char minimum.
    assert extract_mentioned_usernames("mail bob@example.com and @ab") == []


def test_extract_handles_empty_text():
    assert extract_mentioned_usernames("") == []
    assert extract_mentioned_usernames(None) == []


# ── notify_mentions (global) ────────────────────────────────────────────


@pytest.mark.django_db
def test_notify_mentions_creates_notification_for_mentioned_user():
    actor = _make_user("actor")
    mentioned = _make_user("alice")
    ref = uuid.uuid4()

    created = notify_mentions(
        text="hey @alice welcome!",
        actor=actor,
        reference_id=ref,
        reference_type="post",
    )

    assert len(created) == 1
    notif = created[0]
    assert notif.user_id == mentioned.id
    assert notif.notification_type == NotificationType.MENTION
    assert notif.sender_id == actor.id
    assert Notification.objects.filter(
        user_id=mentioned.id, notification_type=NotificationType.MENTION, reference_id=ref
    ).exists()


@pytest.mark.django_db
def test_notify_mentions_skips_self_mention():
    actor = _make_user("selfie")

    created = notify_mentions(
        text="note to @selfie",
        actor=actor,
        reference_id=uuid.uuid4(),
        reference_type="post",
    )

    assert created == []


@pytest.mark.django_db
def test_notify_mentions_ignores_unknown_usernames():
    actor = _make_user("actor")

    created = notify_mentions(
        text="hi @ghost_who_does_not_exist",
        actor=actor,
        reference_id=uuid.uuid4(),
        reference_type="post",
    )

    assert created == []


# ── notify_mentions (circle-scoped) ─────────────────────────────────────


@pytest.mark.django_db
def test_notify_mentions_circle_scope_only_notifies_members():
    from core.circles.models import Circle, CircleMembership

    actor = _make_user("actor")
    member = _make_user("member")
    outsider = _make_user("outsider")

    circle = Circle.objects.create(name="Prayer Circle", description="d", status="active")
    CircleMembership.objects.create(circle=circle, user=member, role="member")

    created = notify_mentions(
        text="welcome @member and @outsider",
        actor=actor,
        reference_id=uuid.uuid4(),
        reference_type="circle_post",
        circle_id=str(circle.id),
    )

    notified_ids = {n.user_id for n in created}
    assert member.id in notified_ids
    assert outsider.id not in notified_ids  # non-member must not be notified

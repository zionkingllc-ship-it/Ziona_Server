"""Anchor like/pray must not crash on Postgres (P1 regression).

`Anchor.objects` (ActiveCreatorContentManager) LEFT-joins `users` via its
active-creator filter. A plain `select_for_update()` then tries to lock the
nullable side of that outer join, which Postgres rejects with
`FOR UPDATE cannot be applied to the nullable side of an outer join` — so every
`likeAnchor`/`prayForAnchor` threw in production. The fix scopes the lock to the
anchors table with `of=("self",)`.

CI runs on SQLite, which silently no-ops `FOR UPDATE`, so a behavioral test alone
can't catch this. The primary guard here asserts the lock is scoped to `self`.
"""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.models.query import QuerySet
from django.utils import timezone

from core.circles.models import Anchor, Circle, CircleMembership
from core.circles.services.anchor_engagement import (
    _locked_anchor,
    like_anchor,
    pray_for_anchor,
)

User = get_user_model()


@pytest.fixture
def member_and_anchor(db):
    """A circle member plus a live anchor in their circle."""
    creator = User.objects.create_user(email="creator@example.com", password="password123")
    member = User.objects.create_user(email="member@example.com", password="password123")
    circle = Circle.objects.create(name="Lock Test Circle", description="x")
    CircleMembership.objects.create(circle=circle, user=creator, role="admin")
    CircleMembership.objects.create(circle=circle, user=member, role="member")
    anchor = Anchor.objects.create(
        circle=circle,
        created_by=creator,
        anchor_type="devotional",
        title="Lock Test Anchor",
        content="content",
        published_at=timezone.now(),
        expires_at=timezone.now() + timedelta(days=1),
    )
    return member, anchor


def test_locked_anchor_scopes_for_update_to_self(member_and_anchor, monkeypatch):
    """Regression guard: the row lock must target only the anchors table.

    Runs on SQLite (FOR UPDATE is a no-op there) by spying on the actual query
    the service builds. Fails on the old plain `select_for_update()` (of=None),
    passes once scoped to of=("self",).
    """
    _, anchor = member_and_anchor
    captured = {}
    original = QuerySet.select_for_update

    def spy(self, *args, **kwargs):
        captured["of"] = kwargs.get("of")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(QuerySet, "select_for_update", spy)

    _locked_anchor(str(anchor.id))

    assert captured["of"] == ("self",)


def test_locked_anchor_raises_for_missing_anchor(db):
    from core.shared.exceptions import ZionaError

    with pytest.raises(ZionaError) as excinfo:
        _locked_anchor("00000000-0000-0000-0000-000000000000")
    assert excinfo.value.code == "ANCHOR_NOT_FOUND"


def test_like_anchor_toggles_state_and_count(member_and_anchor):
    member, anchor = member_and_anchor

    first = like_anchor(user_id=str(member.id), anchor_id=str(anchor.id))
    assert first == {"liked": True, "anchor_liked_count": 1}

    second = like_anchor(user_id=str(member.id), anchor_id=str(anchor.id))
    assert second == {"liked": False, "anchor_liked_count": 0}


def test_pray_for_anchor_toggles_state_and_count(member_and_anchor):
    member, anchor = member_and_anchor

    first = pray_for_anchor(user_id=str(member.id), anchor_id=str(anchor.id))
    assert first == {"prayed": True, "prayed_count": 1}

    second = pray_for_anchor(user_id=str(member.id), anchor_id=str(anchor.id))
    assert second == {"prayed": False, "prayed_count": 0}


@pytest.mark.skipif(
    connection.vendor != "postgresql",
    reason="Only Postgres enforces FOR UPDATE on the nullable side of an outer join.",
)
def test_like_anchor_executes_for_update_on_postgres(member_and_anchor):
    """True reproduction: on Postgres this executes a real FOR UPDATE and would
    raise NotSupportedError on the pre-fix query. Skipped on SQLite CI."""
    member, anchor = member_and_anchor

    assert like_anchor(user_id=str(member.id), anchor_id=str(anchor.id))["liked"] is True
    assert like_anchor(user_id=str(member.id), anchor_id=str(anchor.id))["liked"] is False

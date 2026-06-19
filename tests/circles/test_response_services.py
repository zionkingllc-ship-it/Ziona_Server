"""
Tests for Phase 3: Response System Services.
Tests for:
- respond_to_anchor
- reply_to_response (including depth limit)
- toggle_reaction
- Trending sort algorithm
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from core.circles.anchor_services import get_active_anchor
from core.circles.models import (
    Anchor,
    AnchorResponse,
    Circle,
    CircleMembership,
)
from core.circles.moderation_services import report_circle_content
from core.circles.response_services import (
    create_reply,
    create_response,
    get_anchor_responses,
    toggle_reaction,
)
from core.circles.services import get_all_circles, get_circle_by_id
from core.shared.exceptions import ZionaError
from core.users.models import User


@pytest.fixture
def test_users(db):
    u1 = User.objects.create_user(email="user1@example.com", password="password123")
    u2 = User.objects.create_user(email="user2@example.com", password="password123")
    u3 = User.objects.create_user(email="user3@example.com", password="password123")
    return [u1, u2, u3]


@pytest.fixture
def circle_with_anchor(db, test_users):
    u1, u2, u3 = test_users
    circle = Circle.objects.create(name="Test Circle", description="Test")
    CircleMembership.objects.create(circle=circle, user=u1, role="admin")
    CircleMembership.objects.create(circle=circle, user=u2, role="member")

    # u3 is NOT a member

    anchor = Anchor.objects.create(
        circle=circle,
        created_by=u1,
        anchor_type="devotional",
        title="Test Anchor",
        content="Anchor Content",
        published_at=timezone.now(),
        expires_at=timezone.now() + timedelta(days=1),
    )
    return circle, anchor


def test_create_response_success(circle_with_anchor, test_users):
    circle, anchor = circle_with_anchor
    u1, u2, u3 = test_users

    response = create_response(
        user_id=u2.id, anchor_id=anchor.id, response_type="reflection", content="Great anchor!"
    )

    assert response.user == u2
    assert response.anchor == anchor
    assert response.response_type == "reflection"
    assert response.parent_response is None


def test_create_response_accepts_trusted_external_image_url(
    circle_with_anchor, test_users, settings, monkeypatch
):
    _, anchor = circle_with_anchor
    _, member, _ = test_users
    settings.MEDIA_URL_ALLOWLIST = ["cdn.example.com"]
    image_url = "https://cdn.example.com/responses/image.jpg"
    response = type(
        "Response",
        (),
        {
            "headers": {"Content-Type": "image/jpeg"},
            "status_code": 200,
            "is_redirect": False,
            "is_permanent_redirect": False,
            "close": lambda self: None,
        },
    )()
    monkeypatch.setattr("core.media.services._head_external_media_url", lambda url: response)

    created = create_response(
        user_id=member.id,
        anchor_id=anchor.id,
        response_type="reflection",
        content="Image response",
        media_url=image_url,
        media_type="image",
    )

    assert created.media_url == image_url
    assert created.media_type == "image"


def test_create_response_rejects_external_video_url(circle_with_anchor, test_users):
    _, anchor = circle_with_anchor
    _, member, _ = test_users

    with pytest.raises(ZionaError) as excinfo:
        create_response(
            user_id=member.id,
            anchor_id=anchor.id,
            response_type="reflection",
            content="Video response",
            media_url="https://cdn.example.com/responses/video.mp4",
            media_type="video",
        )

    assert excinfo.value.code == "EXTERNAL_VIDEO_NOT_ALLOWED"


def test_create_response_not_member_raises(circle_with_anchor, test_users):
    circle, anchor = circle_with_anchor
    u1, u2, u3 = test_users

    with pytest.raises(ZionaError) as excinfo:
        create_response(
            user_id=u3.id,  # u3 is not a member
            anchor_id=anchor.id,
            response_type="prayer",
            content="Can I respond?",
        )
    assert excinfo.value.code == "NOT_CIRCLE_MEMBER"


def test_create_reply_success(circle_with_anchor, test_users):
    circle, anchor = circle_with_anchor
    u1, u2, u3 = test_users

    parent = create_response(
        user_id=u1.id, anchor_id=anchor.id, response_type="reflection", content="Parent"
    )
    reply = create_reply(user_id=u2.id, parent_response_id=parent.id, content="I agree")

    assert reply.parent_response == parent
    assert reply.response_type == "reply"
    assert reply.anchor == anchor


def test_threading_depth_exceeded(circle_with_anchor, test_users):
    circle, anchor = circle_with_anchor
    u1, u2, u3 = test_users

    parent = create_response(
        user_id=u1.id, anchor_id=anchor.id, response_type="reflection", content="Parent"
    )
    reply1 = create_reply(user_id=u2.id, parent_response_id=parent.id, content="Reply Level 1")

    # Attempt to reply to the reply (3rd level)
    with pytest.raises(ZionaError) as excinfo:
        create_reply(user_id=u1.id, parent_response_id=reply1.id, content="Reply Level 2")
    assert excinfo.value.code == "THREADING_DEPTH_EXCEEDED"


def test_toggle_reaction(circle_with_anchor, test_users):
    circle, anchor = circle_with_anchor
    u1, u2, u3 = test_users

    response = create_response(
        user_id=u1.id, anchor_id=anchor.id, response_type="reflection", content="Parent"
    )

    # 1. Add reaction
    r1 = toggle_reaction(u2.id, response.id, "amen")
    assert r1 is not None
    assert r1.reaction_type == "amen"
    response.refresh_from_db()
    assert response.reaction_count == 1

    # 2. Change reaction type
    r2 = toggle_reaction(u2.id, response.id, "encouraged")
    assert r2.reaction_type == "encouraged"
    response.refresh_from_db()
    assert response.reaction_count == 1  # still 1 total

    # 3. Toggle off (remove)
    r3 = toggle_reaction(u2.id, response.id, "encouraged")
    assert r3 is None
    response.refresh_from_db()
    assert response.reaction_count == 0


def test_non_member_cannot_toggle_reaction(circle_with_anchor, test_users):
    _circle, anchor = circle_with_anchor
    author, _member, outsider = test_users

    response = create_response(
        user_id=author.id,
        anchor_id=anchor.id,
        response_type="reflection",
        content="Parent",
    )

    with pytest.raises(ZionaError) as excinfo:
        toggle_reaction(outsider.id, response.id, "amen")

    assert excinfo.value.code == "NOT_CIRCLE_MEMBER"


def test_trending_sort_algorithm(circle_with_anchor, test_users):
    circle, anchor = circle_with_anchor
    u1, u2, u3 = test_users

    now = timezone.now()

    # Create 3 responses at different times
    # r1: 10 hours ago, 1 reaction -> Trending Score: (1 * 2) - 10 = -8
    r1 = AnchorResponse.objects.create(
        user=u1,
        anchor=anchor,
        content="Oldest",
        created_at=now - timedelta(hours=10),
        reaction_count=1,
    )
    # The created_at auto_now_add overrides the create() kwarg passing unless we update it
    AnchorResponse.objects.filter(id=r1.id).update(created_at=now - timedelta(hours=10))

    # r2: 5 hours ago, 5 reactions -> Trending Score: (5 * 2) - 5 = 5
    r2 = AnchorResponse.objects.create(user=u2, anchor=anchor, content="Middle", reaction_count=5)
    AnchorResponse.objects.filter(id=r2.id).update(created_at=now - timedelta(hours=5))

    # r3: 1 hour ago, 2 reactions -> Trending Score: (2 * 2) - 1 = 3
    r3 = AnchorResponse.objects.create(user=u1, anchor=anchor, content="Newest", reaction_count=2)
    AnchorResponse.objects.filter(id=r3.id).update(created_at=now - timedelta(hours=1))

    responses = get_anchor_responses(anchor.id, viewer_id=u1.id, sort="TRENDING")

    assert len(responses) == 3
    # r2 (score 5) > r3 (score 3) > r1 (score -8)
    assert responses[0].id == r2.id
    assert responses[1].id == r3.id
    assert responses[2].id == r1.id


def test_non_member_cannot_view_anchor_responses(circle_with_anchor, test_users):
    _circle, anchor = circle_with_anchor
    author, _member, outsider = test_users

    create_response(
        user_id=author.id,
        anchor_id=anchor.id,
        response_type="reflection",
        content="Members only response",
    )

    assert get_anchor_responses(anchor.id, viewer_id=outsider.id) == []


def test_moderation_auto_hide(circle_with_anchor, test_users):
    circle, anchor = circle_with_anchor
    u1, u2, u3 = test_users

    response = create_response(
        user_id=u1.id, anchor_id=anchor.id, response_type="reflection", content="Bad message"
    )

    # 1 report
    report_circle_content(u1.id, "response", response.id, "Spam", circle.id)
    response.refresh_from_db()
    assert response.deleted_at is None

    # 2 reports
    report_circle_content(u2.id, "response", response.id, "Spam", circle.id)
    response.refresh_from_db()
    assert response.deleted_at is None

    # 3 reports -> should auto hide
    # Make sure we add a 4th user for 3 distinct reports
    u4 = User.objects.create_user(email="user4@example.com", password="password123")
    CircleMembership.objects.create(circle=circle, user=u4, role="member")
    report_circle_content(u4.id, "response", response.id, "Spam", circle.id)

    response.refresh_from_db()
    assert response.deleted_at is not None  # Soft deleted


def test_reported_response_hidden_for_reporter_only(circle_with_anchor, test_users):
    circle, anchor = circle_with_anchor
    author, reporter, _ = test_users

    response = create_response(
        user_id=author.id,
        anchor_id=anchor.id,
        response_type="reflection",
        content="Keep going.",
    )

    report_circle_content(reporter.id, "response", response.id, "Spam", circle.id)

    reporter_visible_ids = [
        item.id for item in get_anchor_responses(anchor.id, viewer_id=reporter.id)
    ]
    author_visible_ids = [item.id for item in get_anchor_responses(anchor.id, viewer_id=author.id)]

    assert response.id not in reporter_visible_ids
    assert response.id in author_visible_ids


def test_non_member_cannot_report_circle_content(circle_with_anchor, test_users):
    circle, anchor = circle_with_anchor
    _author, _member, outsider = test_users

    with pytest.raises(ZionaError) as excinfo:
        report_circle_content(outsider.id, "anchor", anchor.id, "Spam", circle.id)

    assert excinfo.value.code == "NOT_CIRCLE_MEMBER"


def test_reported_anchor_hidden_for_reporter_only(circle_with_anchor, test_users):
    circle, anchor = circle_with_anchor
    author, reporter, _ = test_users

    report_circle_content(reporter.id, "anchor", anchor.id, "Spam", circle.id)

    assert get_active_anchor(circle.id, viewer_id=reporter.id) is None
    assert get_active_anchor(circle.id, viewer_id=author.id).id == anchor.id


def test_reported_circle_hidden_for_reporter_only(circle_with_anchor, test_users):
    circle, _ = circle_with_anchor
    author, reporter, _ = test_users

    report_circle_content(reporter.id, "circle", circle.id, "Spam", circle.id)

    reporter_circle_ids = [str(item.id) for item in get_all_circles(viewer_id=reporter.id)]
    author_circle_ids = [str(item.id) for item in get_all_circles(viewer_id=author.id)]

    assert str(circle.id) not in reporter_circle_ids
    assert str(circle.id) in author_circle_ids
    assert get_circle_by_id(circle.id, viewer_id=reporter.id) is None
    assert get_circle_by_id(circle.id, viewer_id=author.id).id == circle.id

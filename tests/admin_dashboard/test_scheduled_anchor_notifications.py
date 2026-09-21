"""The overdue-anchor safety net must notify, not just post.

post_scheduled_anchor notifies on an ETA task, but every Render deploy restarts
the broker and drops pending ETAs. Anchors then land in check_scheduled_anchors,
which posted them silently — members were never told a new anchor existed.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from core.admin_dashboard.tasks import check_scheduled_anchors
from core.circles.models import Anchor, Circle, CircleMembership
from core.notifications.models import Notification, NotificationType


@pytest.fixture
def overdue_anchor(db, create_user):
    author = create_user(email="author@x.com", username="author")
    member = create_user(email="member@x.com", username="member")
    circle = Circle.objects.create(name="Safety Net Circle", description="x")
    CircleMembership.objects.create(circle=circle, user=author, role="admin")
    CircleMembership.objects.create(circle=circle, user=member, role="member")

    anchor = Anchor.objects.create(
        circle=circle,
        created_by=author,
        anchor_type="devotional",
        title="Overdue Anchor",
        content="Body",
        anchor_status="scheduled",
        scheduled_for=timezone.now() - timedelta(minutes=10),
        published_at=timezone.now() - timedelta(minutes=10),
        expires_at=timezone.now() + timedelta(days=1),
    )
    return circle, anchor, member


def test_overdue_anchor_notifies_circle_members(overdue_anchor):
    circle, anchor, member = overdue_anchor

    check_scheduled_anchors()

    anchor.refresh_from_db()
    assert anchor.anchor_status == "posted"

    notification = Notification.objects.filter(
        user_id=member.id,
        notification_type=NotificationType.NEW_ANCHOR,
    ).first()
    assert notification is not None
    assert str(notification.reference_id) == str(anchor.id)
    # Stored lowercase so the mobile route map and the Circles filter match it.
    assert notification.reference_type == "anchor"


def test_overdue_anchor_destination_reaches_the_circle(overdue_anchor):
    circle, anchor, member = overdue_anchor

    check_scheduled_anchors()

    notification = Notification.objects.get(
        user_id=member.id, notification_type=NotificationType.NEW_ANCHOR
    )
    from core.notifications.services import build_notification_destination

    destination = build_notification_destination(
        notification_type=notification.notification_type,
        reference_type=notification.reference_type,
        reference_id=str(notification.reference_id),
    )
    assert destination["circleId"] == str(circle.id)


def test_running_the_safety_net_twice_notifies_once(overdue_anchor):
    """Re-running must not re-notify: the anchor is no longer 'scheduled'."""
    _circle, _anchor, member = overdue_anchor

    check_scheduled_anchors()
    check_scheduled_anchors()

    assert (
        Notification.objects.filter(
            user_id=member.id, notification_type=NotificationType.NEW_ANCHOR
        ).count()
        == 1
    )

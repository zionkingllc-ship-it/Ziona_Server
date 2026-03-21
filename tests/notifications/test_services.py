import uuid

import pytest
from django.contrib.auth import get_user_model

from core.notifications.models import (
    DeviceToken,
    Notification,
    NotificationPreference,
    NotificationType,
)
from core.notifications.services import (
    batch_like_notifications,
    create_admin_announcement,
    create_notification,
    get_unread_count,
    mark_as_read,
    register_device_token,
)

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email="testuser@example.com",
        username="testuser",
        password="password123",
        firebase_uid="firebase123",
    )


@pytest.fixture
def other_user(db):
    return User.objects.create_user(
        email="other@example.com",
        username="other",
        password="password123",
        firebase_uid="firebase456",
    )


def test_create_notification(db, user):
    ref_id = uuid.uuid4()
    notif = create_notification(
        user_id=user.id,
        type_str=NotificationType.NEW_ANCHOR,
        reference_id=ref_id,
        reference_type="anchor",
        message="New anchor!",
    )
    assert notif is not None
    assert notif.user_id == user.id
    assert notif.notification_type == NotificationType.NEW_ANCHOR


def test_create_notification_preferences_disabled(db, user):
    NotificationPreference.objects.create(user=user, anchor_notifications=False)

    ref_id = uuid.uuid4()
    notif = create_notification(
        user_id=user.id,
        type_str=NotificationType.NEW_ANCHOR,
        reference_id=ref_id,
        reference_type="anchor",
        message="New anchor!",
    )
    assert notif is None


def test_create_notification_duplicate_spam_prevention(db, user):
    ref_id = uuid.uuid4()
    notif1 = create_notification(
        user_id=user.id,
        type_str=NotificationType.REPLY_COMMENT,
        reference_id=ref_id,
        reference_type="comment",
        message="Test reply",
    )
    assert notif1 is not None

    # Exact same request should be blocked
    notif2 = create_notification(
        user_id=user.id,
        type_str=NotificationType.REPLY_COMMENT,
        reference_id=ref_id,
        reference_type="comment",
        message="Test reply again",
    )
    assert notif2 is None


def test_mark_as_read(db, user):
    notif = Notification.objects.create(
        user_id=user.id, notification_type=NotificationType.MENTION, message="Hello"
    )
    assert not notif.is_read

    success = mark_as_read(notif.id, user.id)
    assert success is True

    notif.refresh_from_db()
    assert notif.is_read


def test_get_unread_count(db, user):
    Notification.objects.create(user=user, notification_type=NotificationType.MENTION, message="1")
    Notification.objects.create(user=user, notification_type=NotificationType.MENTION, message="2")
    n3 = Notification.objects.create(
        user=user, notification_type=NotificationType.MENTION, message="3"
    )

    mark_as_read(n3.id, user.id)

    assert get_unread_count(user.id) == 2


def test_register_device_token_limit(db, user):
    for i in range(5):
        register_device_token(user.id, f"token_{i}", "ios")

    assert DeviceToken.objects.filter(user=user).count() == 5

    # Add a 6th device — should replace the oldest one
    register_device_token(user.id, "token_6", "ios")
    # In sqlite/fast tests, created_at is identical, but replacing occurs
    assert DeviceToken.objects.filter(user=user).count() == 5
    assert DeviceToken.objects.filter(token="token_6").exists()


def test_batch_like_notifications(db, user, other_user):
    post_id = uuid.uuid4()

    batch_like_notifications(
        actor_username=other_user.username,
        recipient_id=user.id,
        reference_id=post_id,
        reference_type="post",
        like_type=NotificationType.LIKE_POST,
    )

    notifs = Notification.objects.filter(user=user)
    assert notifs.count() == 1
    assert notifs.first().message == f"{other_user.username} liked your post"

    batch_like_notifications(
        actor_username="third_user",
        recipient_id=user.id,
        reference_id=post_id,
        reference_type="post",
        like_type=NotificationType.LIKE_POST,
    )

    notifs = Notification.objects.filter(user=user)
    assert notifs.count() == 1  # Updated
    assert "and 1 others liked your post" in notifs.first().message


def test_create_admin_announcement(db, user, other_user):
    create_admin_announcement(
        admin_id=1, message="System Maintenance", target_users=[user.id, other_user.id]
    )

    assert Notification.objects.filter(
        user=user, notification_type=NotificationType.ADMIN_ANNOUNCEMENT
    ).exists()
    assert Notification.objects.filter(
        user=other_user, notification_type=NotificationType.ADMIN_ANNOUNCEMENT
    ).exists()

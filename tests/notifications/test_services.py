import uuid
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from core.notifications.models import (
    DeviceToken,
    Notification,
    NotificationMutedUser,
    NotificationPreference,
    NotificationType,
)
from core.notifications.services import (
    batch_like_notifications,
    build_notification_destination,
    create_admin_announcement,
    create_notification,
    get_notifications,
    get_unread_count,
    mark_as_read,
    register_device_token,
    send_push_notification,
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
    NotificationPreference.objects.create(user=user, circle_anchor_post=False)

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


def test_muted_sender_is_excluded_from_list_unread_count_and_creation(db, user, other_user):
    visible_sender = User.objects.create_user(
        email="visible@example.com",
        username="visible",
        password="password123",
    )
    NotificationMutedUser.objects.create(user=user, muted_user=other_user)
    Notification.objects.create(
        user=user,
        sender=other_user,
        notification_type=NotificationType.LIKE_POST,
        reference_id=uuid.uuid4(),
        reference_type="post",
        message="muted like",
    )
    visible_notification = Notification.objects.create(
        user=user,
        sender=visible_sender,
        notification_type=NotificationType.LIKE_POST,
        reference_id=uuid.uuid4(),
        reference_type="post",
        message="visible like",
    )

    assert list(get_notifications(user.id).values_list("id", flat=True)) == [
        visible_notification.id
    ]
    assert get_unread_count(user.id) == 1

    assert (
        create_notification(
            user_id=user.id,
            sender_id=other_user.id,
            type_str=NotificationType.MENTION,
            reference_id=uuid.uuid4(),
            reference_type="post",
            message="muted mention",
        )
        is None
    )
    assert Notification.objects.filter(user=user).count() == 2


def test_muted_sender_push_is_suppressed(db, user, other_user, monkeypatch):
    DeviceToken.objects.create(user=user, token="fcm-token", platform="android")
    NotificationMutedUser.objects.create(user=user, muted_user=other_user)
    calls = []

    monkeypatch.setattr(
        "core.notifications.services.send_fcm_notification",
        lambda tokens, title, body, data: calls.append(tokens) or {},
    )

    send_push_notification(
        user_id=user.id,
        title="Muted",
        body="Muted body",
        data={"type": NotificationType.LIKE_POST, "senderId": str(other_user.id)},
    )

    assert calls == []


def test_notification_category_filtering(db, user, other_user):
    interaction = Notification.objects.create(
        user=user,
        sender=other_user,
        notification_type=NotificationType.LIKE_POST,
        reference_id=uuid.uuid4(),
        reference_type="post",
        message="liked",
    )
    circle = Notification.objects.create(
        user=user,
        sender=other_user,
        notification_type=NotificationType.NEW_CIRCLE_POST,
        reference_id=uuid.uuid4(),
        reference_type="circle_post",
        message="circle",
    )
    update = Notification.objects.create(
        user=user,
        notification_type=NotificationType.ADMIN_ANNOUNCEMENT,
        message="update",
    )

    assert [n.id for n in get_notifications(user.id, category="INTERACTIONS")] == [interaction.id]
    assert [n.id for n in get_notifications(user.id, category="circles")] == [circle.id]
    assert [n.id for n in get_notifications(user.id, category="updates")] == [update.id]
    assert {n.id for n in get_notifications(user.id, category="all")} == {
        update.id,
        circle.id,
        interaction.id,
    }


def test_notification_user_viewer_state_resolves_without_n_plus_one(
    db, user, other_user, django_assert_num_queries
):
    from core.follows.models import Follow
    from core.notifications.schema import NotificationItem

    followed_by_sender = User.objects.create_user(
        email="followed-by@example.com",
        username="followedby",
        password="password123",
    )
    Follow.objects.create(follower=user, following=other_user)
    Follow.objects.create(follower=followed_by_sender, following=user)
    Notification.objects.create(
        user=user,
        sender=other_user,
        notification_type=NotificationType.LIKE_POST,
        reference_id=uuid.uuid4(),
        reference_type="post",
        message="liked",
    )
    Notification.objects.create(
        user=user,
        sender=followed_by_sender,
        notification_type=NotificationType.REPLY_POST,
        reference_id=uuid.uuid4(),
        reference_type="comment",
        message="commented",
    )

    notifications = list(get_notifications(user.id))

    with django_assert_num_queries(0):
        states = {
            NotificationItem.from_instance(notification).user().username: (
                NotificationItem.from_instance(notification).user().viewer_state.is_following,
                NotificationItem.from_instance(notification).user().viewer_state.is_followed_by,
                NotificationItem.from_instance(notification).user().viewer_state.is_owner,
            )
            for notification in notifications
        }

    assert states["other"] == (True, False, False)
    assert states["followedby"] == (False, True, False)


def test_all_notification_types_produce_expected_destinations(db, user, other_user, settings):
    from core.circles.models import Anchor, Circle, CirclePost
    from core.engagement.models import Comment
    from core.posts.models import Post

    settings.APP_SHARE_BASE_URL = "https://share.ziona.test"
    post = Post.objects.create(user=user, post_type="text", caption="Post")
    comment = Comment.objects.create(post=post, user=other_user, text="Comment")
    circle = Circle.objects.create(
        name="Destination Circle",
        description="Destination",
        cover_image="https://example.com/cover.jpg",
        created_by=user,
    )
    circle_post = CirclePost.objects.create(circle=circle, user=user, text="Circle post")
    anchor = Anchor.objects.create(
        circle=circle,
        title="Anchor",
        content="Anchor content",
        anchor_type="text",
        created_by=user,
        published_at=timezone.now(),
        expires_at=timezone.now() + timedelta(hours=24),
    )

    cases = [
        (NotificationType.LIKE_POST, "post", post.id, "post_detail", "post"),
        (NotificationType.REPLY_POST, "comment", comment.id, "comment_thread", "comment"),
        (NotificationType.LIKE_COMMENT, "comment", comment.id, "comment_thread", "comment"),
        (NotificationType.MENTION, "post", post.id, "post_detail", "post"),
        (NotificationType.NEW_FOLLOWER, "user", other_user.id, "profile", "user"),
        (
            NotificationType.NEW_CIRCLE_POST,
            "circle_post",
            circle_post.id,
            "circle_post_detail",
            "circle_post",
        ),
        (NotificationType.NEW_ANCHOR, "anchor", anchor.id, "anchor_detail", "anchor"),
        (
            NotificationType.ADMIN_ANNOUNCEMENT,
            "",
            None,
            "notification_detail",
            "notification",
        ),
    ]

    for notification_type, reference_type, reference_id, route, entity_type in cases:
        destination = build_notification_destination(
            notification_type=notification_type,
            reference_type=reference_type,
            reference_id=reference_id,
            notification_id=uuid.uuid4(),
        )
        assert destination["route"] == route
        assert destination["entityType"] == entity_type


def test_register_device_token_limit(db, user):
    for i in range(5):
        register_device_token(user.id, f"token_{i}", "ios")

    assert DeviceToken.objects.filter(user=user).count() == 5

    # Add a 6th device — should replace the oldest one
    register_device_token(user.id, "token_6", "ios")
    # In sqlite/fast tests, created_at is identical, but replacing occurs
    assert DeviceToken.objects.filter(user=user).count() == 5
    assert DeviceToken.objects.filter(token="token_6").exists()


def test_register_device_token_is_idempotent_for_same_user(db, user):
    register_device_token(user.id, "ExponentPushToken[same-device]", "ios")
    register_device_token(user.id, "ExponentPushToken[same-device]", "android")

    token = DeviceToken.objects.get(token="ExponentPushToken[same-device]")
    assert token.user == user
    assert token.platform == "android"
    assert token.is_active is True
    assert DeviceToken.objects.filter(token="ExponentPushToken[same-device]").count() == 1


def test_register_device_token_transfers_same_device_to_new_user(db, user, other_user):
    register_device_token(user.id, "ExponentPushToken[shared-device]", "ios")
    register_device_token(other_user.id, "ExponentPushToken[shared-device]", "ios")

    token = DeviceToken.objects.get(token="ExponentPushToken[shared-device]")
    assert token.user == other_user
    assert token.is_active is True
    assert DeviceToken.objects.filter(user=user).count() == 0


def test_register_device_token_keeps_transferred_token_when_enforcing_limit(db, user, other_user):
    register_device_token(other_user.id, "ExponentPushToken[shared-device]", "ios")
    for i in range(5):
        register_device_token(user.id, f"ExponentPushToken[user-device-{i}]", "ios")

    register_device_token(user.id, "ExponentPushToken[shared-device]", "ios")

    assert DeviceToken.objects.filter(user=user).count() == 5
    assert DeviceToken.objects.filter(user=user, token="ExponentPushToken[shared-device]").exists()
    assert not DeviceToken.objects.filter(user=other_user).exists()


class TestRegisterDeviceTokenLocking:
    """Deadlock regression: concurrent registrations must lock rows in one order.

    Two concurrent registerDeviceToken calls used to deadlock on Postgres: each
    locked its own token row (update_or_create), then swept the user's other
    rows (limit enforcement) that the other transaction held. The fix takes
    every needed lock up front, ordered by pk. SQLite no-ops FOR UPDATE, so
    these assert the lock query's shape and coverage rather than real blocking.
    """

    def test_lock_queryset_is_for_update_in_pk_order(self, db, user):
        from core.notifications.services import _registration_lock_queryset

        qs = _registration_lock_queryset(user_id=user.id, token="some-token")

        assert qs.query.select_for_update is True
        assert qs.query.order_by == ("pk",)

    def test_lock_covers_users_rows_and_foreign_owned_incoming_token(self, db, user, other_user):
        """The sweep must include the transfer case: the incoming token's row
        even when it currently belongs to another user."""
        from core.notifications.services import _registration_lock_queryset

        register_device_token(user.id, "user-token-1", "ios")
        register_device_token(user.id, "user-token-2", "ios")
        register_device_token(other_user.id, "shared-token", "ios")
        register_device_token(other_user.id, "unrelated-token", "ios")

        locked = list(_registration_lock_queryset(user_id=user.id, token="shared-token"))
        locked_tokens = {t.token for t in locked}

        assert locked_tokens == {"user-token-1", "user-token-2", "shared-token"}
        assert "unrelated-token" not in locked_tokens  # untouched rows stay unlocked
        assert [t.pk for t in locked] == sorted(t.pk for t in locked)

    def test_register_takes_locks_before_writing(self, db, user, monkeypatch):
        """The pre-lock must run (and run first) on every registration."""
        import core.notifications.services as services

        calls = []
        original = services._lock_registration_rows

        def spy(**kwargs):
            calls.append(kwargs)
            return original(**kwargs)

        monkeypatch.setattr(services, "_lock_registration_rows", spy)

        register_device_token(user.id, "lock-order-token", "ios")

        assert calls == [{"user_id": user.id, "token": "lock-order-token"}]
        assert DeviceToken.objects.filter(token="lock-order-token", user=user).exists()

    def test_limit_enforcement_takes_no_locks_of_its_own(self, db):
        """Re-locking inside the sweep was half of the deadlock cycle."""
        import inspect

        from core.notifications.services import _enforce_device_token_limit

        assert "select_for_update" not in inspect.getsource(_enforce_device_token_limit)


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


def test_create_admin_announcement_queues_bounded_push_batch(
    db, user, other_user, monkeypatch, django_capture_on_commit_callbacks
):
    queued_batches = []

    monkeypatch.setattr(
        "core.notifications.tasks.send_admin_announcement_push_batch.apply_async",
        lambda **kwargs: queued_batches.append(kwargs["kwargs"]),
    )

    with django_capture_on_commit_callbacks(execute=True):
        create_admin_announcement(
            admin_id=1, message="System Maintenance", target_users=[user.id, other_user.id]
        )

    assert len(queued_batches) == 1
    assert set(queued_batches[0]["user_ids"]) == {str(user.id), str(other_user.id)}
    assert queued_batches[0]["data"]["type"] == NotificationType.ADMIN_ANNOUNCEMENT


def test_register_device_token_logs_without_full_token(db, user, monkeypatch):
    log_calls = []

    def fake_info(message, *args, **kwargs):
        log_calls.append((message, kwargs.get("extra", {})))

    monkeypatch.setattr("core.notifications.services.logger.info", fake_info)

    register_device_token(user.id, "ExponentPushToken[private-secret-token]", "ios")

    assert any(message == "device_token_registered" for message, _ in log_calls)
    assert "private-secret-token" not in str(log_calls)


def test_send_push_notification_logs_provider_summary(db, user, monkeypatch):
    register_device_token(user.id, "ExponentPushToken[device-1]", "ios")
    log_calls = []

    def fake_info(message, *args, **kwargs):
        log_calls.append((message, kwargs.get("extra", {})))

    def fake_send(tokens, title, body, data):
        return {"success_count": 1, "failure_count": 0, "invalid_token_count": 0}

    monkeypatch.setattr("core.notifications.services.send_fcm_notification", fake_send)
    monkeypatch.setattr("core.notifications.services.logger.info", fake_info)

    send_push_notification(
        user_id=user.id,
        title="Hello",
        body="World",
        data={"type": NotificationType.ADMIN_ANNOUNCEMENT, "reference_id": "abc"},
    )

    messages = [message for message, _ in log_calls]
    assert "push_notification_dispatch_started" in messages
    assert "push_notification_dispatch_finished" in messages


@pytest.mark.django_db
def test_create_notification_push_payload_uses_camelcase_keys(
    user, monkeypatch, django_capture_on_commit_callbacks, settings
):
    """The FCM push payload must use camelCase keys the mobile tap-handler reads.

    Push is now queued to Celery after commit, so the on-commit callback is
    executed here and the task's payload is what we assert on.
    """
    from core.posts.models import Post

    captured = {}

    def fake_apply_async(*args, **kwargs):
        captured["data"] = kwargs["kwargs"]["data"]

    monkeypatch.setattr(
        "core.notifications.tasks.send_push_notification_task.apply_async", fake_apply_async
    )

    settings.APP_SHARE_BASE_URL = "https://share.ziona.test"
    post = Post.objects.create(user=user, post_type="text", caption="Push target")
    ref = post.id
    with django_capture_on_commit_callbacks(execute=True):
        create_notification(
            user_id=user.id,
            type_str=NotificationType.LIKE_POST,
            reference_id=ref,
            reference_type="post",
            message="liked your post",
            respect_preferences=False,
            bypass_duplicate_check=True,
        )

    data = captured["data"]
    assert data["referenceType"] == "post"
    assert data["referenceId"] == str(ref)
    assert data["type"] == NotificationType.LIKE_POST
    assert data["screen"] == "NotificationDetail"
    assert data["destinationRoute"] == "post_detail"
    assert data["destinationEntityType"] == "post"
    assert data["destinationEntityId"] == str(ref)
    assert data["destinationSecondaryEntityId"] == ""
    assert data["deepLink"] == f"https://share.ziona.test/post/{ref}"
    # Old snake_case keys must be gone (they broke mobile deep-linking).
    assert "reference_type" not in data
    assert "reference_id" not in data


@pytest.mark.django_db
def test_notification_destination_falls_back_for_missing_reference(settings):
    settings.APP_SHARE_BASE_URL = "https://share.ziona.test"
    missing_ref = uuid.uuid4()

    destination = build_notification_destination(
        notification_type=NotificationType.REPLY_POST,
        reference_type="comment",
        reference_id=missing_ref,
        notification_id=uuid.uuid4(),
    )

    assert destination["route"] == "notification_detail"
    assert destination["entityType"] == "comment"
    assert destination["entityId"] == str(missing_ref)
    assert destination["deepLink"] == ""


@pytest.mark.django_db
def test_push_is_queued_not_sent_inline(user, monkeypatch, django_capture_on_commit_callbacks):
    """The FCM round-trip must not happen inside the request."""
    calls = {"queued": 0, "inline": 0}
    monkeypatch.setattr(
        "core.notifications.tasks.send_push_notification_task.apply_async",
        lambda *a, **k: calls.__setitem__("queued", calls["queued"] + 1),
    )
    monkeypatch.setattr(
        "core.notifications.services.send_fcm_notification",
        lambda *a, **k: calls.__setitem__("inline", calls["inline"] + 1),
    )

    with django_capture_on_commit_callbacks(execute=True):
        create_notification(
            user_id=user.id,
            type_str=NotificationType.LIKE_POST,
            reference_id=uuid.uuid4(),
            reference_type="post",
            message="liked your post",
            respect_preferences=False,
            bypass_duplicate_check=True,
        )

    assert calls["queued"] == 1
    assert calls["inline"] == 0  # never sent on the request thread


@pytest.mark.django_db
def test_push_is_not_queued_until_transaction_commits(user, monkeypatch):
    """A rolled-back transaction must not send a push for a vanished notification."""
    queued = []
    monkeypatch.setattr(
        "core.notifications.tasks.send_push_notification_task.apply_async",
        lambda *a, **k: queued.append(k),
    )

    create_notification(
        user_id=user.id,
        type_str=NotificationType.LIKE_POST,
        reference_id=uuid.uuid4(),
        reference_type="post",
        message="liked your post",
        respect_preferences=False,
        bypass_duplicate_check=True,
    )

    # The test's surrounding transaction never commits, so nothing was dispatched.
    assert queued == []


@pytest.mark.django_db
def test_push_falls_back_to_inline_when_broker_is_unreachable(
    user, monkeypatch, django_capture_on_commit_callbacks
):
    """A dead broker must not silently swallow the notification."""
    sent_inline = []

    def broker_down(*args, **kwargs):
        raise OSError("broker unreachable")

    monkeypatch.setattr(
        "core.notifications.tasks.send_push_notification_task.apply_async", broker_down
    )
    monkeypatch.setattr(
        "core.notifications.services.send_fcm_notification",
        lambda tokens, title, body, data: sent_inline.append(title) or {},
    )
    DeviceToken.objects.create(user=user, token="fcm-token-abc", platform="ios", is_active=True)

    with django_capture_on_commit_callbacks(execute=True):
        create_notification(
            user_id=user.id,
            type_str=NotificationType.LIKE_POST,
            reference_id=uuid.uuid4(),
            reference_type="post",
            message="liked your post",
            title="New Like",
            respect_preferences=False,
            bypass_duplicate_check=True,
        )

    assert sent_inline == ["New Like"]

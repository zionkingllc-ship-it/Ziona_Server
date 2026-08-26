"""Tests for FollowService — follow, unfollow, suggestions."""

from datetime import timedelta

import pytest
from django.utils import timezone

from core.follows.services import FollowService
from core.shared.exceptions import FollowError


@pytest.fixture
def user_a(create_user):
    return create_user(email="a@test.com", username="user_a")


@pytest.fixture
def user_b(create_user):
    return create_user(email="b@test.com", username="user_b")


@pytest.fixture
def user_c(create_user):
    return create_user(email="c@test.com", username="user_c")


class TestFollowUser:
    """Tests for follow operations."""

    def test_follow_success(self, user_a, user_b):
        result = FollowService.follow_user(str(user_a.id), str(user_b.id))
        assert result.success is True
        assert result.following is True

    def test_follow_creates_profile_notification(self, user_a, user_b):
        from core.notifications.models import Notification, NotificationType

        FollowService.follow_user(str(user_a.id), str(user_b.id))

        notification = Notification.objects.get(
            user=user_b,
            sender=user_a,
            notification_type=NotificationType.NEW_FOLLOWER,
        )
        assert notification.reference_type == "user"
        assert notification.reference_id == user_a.id
        assert notification.title == "New Follower"
        assert notification.message == "user_a started following you"

    def test_self_follow_blocked(self, user_a):
        with pytest.raises(FollowError) as exc:
            FollowService.follow_user(str(user_a.id), str(user_a.id))
        assert exc.value.code == "CANNOT_FOLLOW_SELF"

    def test_double_follow_blocked(self, user_a, user_b):
        FollowService.follow_user(str(user_a.id), str(user_b.id))
        with pytest.raises(FollowError) as exc:
            FollowService.follow_user(str(user_a.id), str(user_b.id))
        assert exc.value.code == "ALREADY_FOLLOWING"

    def test_unfollow(self, user_a, user_b):
        FollowService.follow_user(str(user_a.id), str(user_b.id))
        result = FollowService.unfollow_user(str(user_a.id), str(user_b.id))
        assert result.success is True
        assert result.following is False

    def test_refollow_within_24_hours_does_not_duplicate_notification(self, user_a, user_b):
        from core.notifications.models import Notification, NotificationType

        FollowService.follow_user(str(user_a.id), str(user_b.id))
        FollowService.unfollow_user(str(user_a.id), str(user_b.id))
        FollowService.follow_user(str(user_a.id), str(user_b.id))

        assert (
            Notification.objects.filter(
                user=user_b,
                sender=user_a,
                notification_type=NotificationType.NEW_FOLLOWER,
            ).count()
            == 1
        )

    def test_refollow_after_24_hours_can_notify_again(self, user_a, user_b):
        from core.notifications.models import Notification, NotificationType

        FollowService.follow_user(str(user_a.id), str(user_b.id))
        Notification.objects.filter(
            user=user_b,
            sender=user_a,
            notification_type=NotificationType.NEW_FOLLOWER,
        ).update(created_at=timezone.now() - timedelta(hours=25))

        FollowService.unfollow_user(str(user_a.id), str(user_b.id))
        FollowService.follow_user(str(user_a.id), str(user_b.id))

        assert (
            Notification.objects.filter(
                user=user_b,
                sender=user_a,
                notification_type=NotificationType.NEW_FOLLOWER,
            ).count()
            == 2
        )

    def test_disabled_follow_preferences_suppress_notification(self, user_a, user_b):
        from core.notifications.models import Notification, NotificationPreference, NotificationType

        NotificationPreference.objects.create(
            user=user_b,
            in_app_new_followers=False,
            interaction_new_follower=True,
        )

        FollowService.follow_user(str(user_a.id), str(user_b.id))

        assert not Notification.objects.filter(
            user=user_b,
            sender=user_a,
            notification_type=NotificationType.NEW_FOLLOWER,
        ).exists()


class TestGetFollowers:
    """Tests for followers/following list retrieval."""

    def test_get_followers(self, user_a, user_b, user_c):
        FollowService.follow_user(str(user_b.id), str(user_a.id))
        FollowService.follow_user(str(user_c.id), str(user_a.id))

        result = FollowService.get_followers(str(user_a.id))
        assert len(result["users"]) == 2

    def test_get_following(self, user_a, user_b, user_c):
        FollowService.follow_user(str(user_a.id), str(user_b.id))
        FollowService.follow_user(str(user_a.id), str(user_c.id))

        result = FollowService.get_following(str(user_a.id))
        assert len(result["users"]) == 2


class TestSuggestedCreators:
    """Tests for creator suggestions."""

    def test_excludes_already_followed(self, user_a, user_b, user_c):
        from core.posts.models import Post

        Post.objects.create(user=user_b, post_type="text", caption="B post")
        Post.objects.create(user=user_c, post_type="text", caption="C post")

        FollowService.follow_user(str(user_a.id), str(user_b.id))
        suggestions = FollowService.get_suggested_creators(str(user_a.id))

        suggestion_ids = [s["user"].id for s in suggestions]
        assert str(user_b.id) not in suggestion_ids
        assert str(user_c.id) in suggestion_ids

    def test_excludes_self(self, user_a):
        suggestions = FollowService.get_suggested_creators(str(user_a.id))
        suggestion_ids = [s["user"].id for s in suggestions]
        assert str(user_a.id) not in suggestion_ids

    def test_suggests_creators_without_posts_for_new_users(self, user_a, user_b):
        suggestions = FollowService.get_suggested_creators(str(user_a.id))
        suggestion_ids = [s["user"].id for s in suggestions]
        assert str(user_b.id) in suggestion_ids

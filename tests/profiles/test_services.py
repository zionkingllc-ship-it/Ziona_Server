"""Tests for ProfileService — profile retrieval and updates."""

import pytest

from core.profiles.services import ProfileService
from core.shared.exceptions import ProfileError


@pytest.fixture
def user_a(create_user):
    return create_user(email="a@test.com", username="user_a")


@pytest.fixture
def user_b(create_user):
    return create_user(email="b@test.com", username="user_b")


class TestGetUserProfile:
    """Tests for profile retrieval."""

    def test_get_own_profile(self, user_a):
        result = ProfileService.get_user_profile(str(user_a.id), viewer_id=str(user_a.id))
        assert result.username == "user_a"
        assert result.is_own_profile is True

    def test_get_other_profile(self, user_a, user_b):
        result = ProfileService.get_user_profile(str(user_a.id), viewer_id=str(user_b.id))
        assert result.username == "user_a"
        assert result.is_own_profile is False
        assert result.is_following is False

    def test_following_state(self, user_a, user_b):
        from core.follows.services import FollowService

        FollowService.follow_user(str(user_b.id), str(user_a.id))
        result = ProfileService.get_user_profile(str(user_a.id), viewer_id=str(user_b.id))
        assert result.is_following is True

    def test_nonexistent_user(self):
        with pytest.raises(ProfileError):
            ProfileService.get_user_profile("00000000-0000-0000-0000-000000000000")


class TestUpdateProfile:
    """Tests for profile updates."""

    def test_update_bio(self, user_a):
        result = ProfileService.update_profile(str(user_a.id), bio="My new bio")
        assert result.bio == "My new bio"

    def test_bio_too_long(self, user_a):
        with pytest.raises(ProfileError):
            ProfileService.update_profile(str(user_a.id), bio="x" * 151)

    def test_update_full_name(self, user_a):
        result = ProfileService.update_profile(str(user_a.id), full_name="New Name")
        assert result.full_name == "New Name"

    def test_update_multiple_fields(self, user_a):
        result = ProfileService.update_profile(
            str(user_a.id),
            bio="Updated bio",
            full_name="Updated Name",
            location="New York",
        )
        assert result.bio == "Updated bio"
        assert result.full_name == "Updated Name"
        assert result.location == "New York"

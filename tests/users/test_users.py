"""Tests for user services — username and DOB."""

import json
from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction
from django.test import Client
from django.utils import timezone

from core.authentication.services import AuthService
from core.authentication.validators import AuthenticationError
from core.users.account_lifecycle import anonymize_user_for_permanent_delete
from core.users.models import User, UsernameReservation
from core.users.selectors import check_username_availability, suggest_usernames
from core.users.services import UserService, UserServiceError
from core.users.username_policy import username_editable_until
from core.users.validators import (
    UsernameValidationError,
    validate_username_format,
    validate_username_not_reserved,
)


class TestUsernameValidation:
    """Test username format validation."""

    def test_valid_username(self):
        """Standard username should pass."""
        validate_username_format("john_doe")

    def test_username_too_short(self):
        """Username under 3 chars should fail."""
        with pytest.raises(UsernameValidationError) as exc_info:
            validate_username_format("ab")
        assert exc_info.value.code == "USERNAME_TOO_SHORT"

    def test_username_too_long(self):
        """Username over 30 chars should fail."""
        with pytest.raises(UsernameValidationError) as exc_info:
            validate_username_format("a" * 31)
        assert exc_info.value.code == "USERNAME_TOO_LONG"

    def test_username_special_chars(self):
        """Username with special chars (not underscore) should fail."""
        with pytest.raises(UsernameValidationError) as exc_info:
            validate_username_format("user@name")
        assert exc_info.value.code == "USERNAME_INVALID_FORMAT"

    def test_username_leading_underscore(self):
        """Username starting with underscore should fail."""
        with pytest.raises(UsernameValidationError) as exc_info:
            validate_username_format("_username")
        assert exc_info.value.code == "USERNAME_INVALID_FORMAT"

    def test_username_consecutive_underscores(self):
        """Username with __ should fail."""
        with pytest.raises(UsernameValidationError) as exc_info:
            validate_username_format("user__name")
        assert exc_info.value.code == "USERNAME_INVALID_FORMAT"

    def test_reserved_username(self):
        """Reserved words should fail."""
        with pytest.raises(UsernameValidationError) as exc_info:
            validate_username_not_reserved("admin")
        assert exc_info.value.code == "USERNAME_RESERVED"


class TestUsernameAvailability:
    """Test username availability checks."""

    def test_available_username(self, db):
        """Unused username should be available."""
        result = check_username_availability("fresh_name")
        assert result["available"] is True

    def test_taken_username(self, create_user):
        """Already-used username should not be available."""
        create_user(username="taken_name")
        result = check_username_availability("taken_name")
        assert result["available"] is False
        assert "taken" in result["reason"].lower()

    def test_invalid_format_not_available(self, db):
        """Badly formatted username should report as unavailable."""
        result = check_username_availability("ab")
        assert result["available"] is False


class TestUsernameSuggestions:
    """Test username suggestion generation."""

    def test_returns_suggestions(self, db):
        """Should return at least 1 suggestion."""
        suggestions = suggest_usernames("john", count=4)
        assert len(suggestions) >= 1
        assert all(isinstance(s, str) for s in suggestions)

    def test_suggestions_are_available(self, db):
        """All suggestions should be available."""
        suggestions = suggest_usernames("testname", count=4)
        for name in suggestions:
            result = check_username_availability(name)
            assert result["available"] is True


class TestSetUsername:
    """Test setting username on a user account."""

    def test_set_username_success(self, create_user):
        """Setting a valid username should update the user."""
        user = create_user(username="user_temp123")
        updated = UserService.set_username(str(user.id), "new_name")
        assert updated.username == "new_name"

    def test_set_username_invalid_format(self, create_user):
        """Setting an invalid username should fail."""
        user = create_user(username="user_temp456")
        with pytest.raises(UserServiceError) as exc_info:
            UserService.set_username(str(user.id), "ab")
        assert exc_info.value.code == "USERNAME_TOO_SHORT"

    def test_set_username_taken(self, create_user):
        """Setting a username that's taken should fail."""
        create_user(email="user1@test.com", username="owner")
        user2 = create_user(email="user2@test.com", username="user_temp789")

        with pytest.raises(UserServiceError) as exc_info:
            UserService.set_username(str(user2.id), "owner")
        assert exc_info.value.code == "USERNAME_TAKEN"

    def test_set_username_reserved(self, create_user):
        """Setting a reserved username should fail."""
        user = create_user(username="user_tempabc")
        with pytest.raises(UserServiceError) as exc_info:
            UserService.set_username(str(user.id), "admin")
        assert exc_info.value.code == "USERNAME_RESERVED"

    def test_three_grace_edits_do_not_start_cooldown(self, create_user):
        user = create_user(username="grace_start")
        for name in ("grace_one", "grace_two", "grace_three"):
            user = UserService.set_username(str(user.id), name)
        assert user.grace_username_changes == 3
        assert user.last_username_change is None
        assert username_editable_until(user) is None

        with pytest.raises(UserServiceError) as exc_info:
            UserService.set_username(str(user.id), "grace_four")
        assert exc_info.value.code == "RATE_LIMIT_EXCEEDED"

    def test_noop_does_not_spend_grace_edit(self, create_user):
        user = create_user(username="noop_handle")
        updated = UserService.set_username(str(user.id), "noop_handle")
        assert updated.grace_username_changes == 0
        assert not UsernameReservation.objects.exists()

    def test_after_grace_first_edit_starts_thirty_day_cooldown(self, create_user):
        user = create_user(username="older_start")
        User.objects.filter(pk=user.pk).update(created_at=timezone.now() - timedelta(hours=49))
        user.refresh_from_db()
        assert username_editable_until(user) is None

        changed = UserService.set_username(str(user.id), "older_new")
        assert changed.last_username_change is not None
        with pytest.raises(UserServiceError) as exc_info:
            UserService.set_username(str(user.id), "older_again")
        assert exc_info.value.code == "RATE_LIMIT_EXCEEDED"

    def test_prior_grace_edit_does_not_lock_after_window(self, create_user):
        user = create_user(username="window_start")
        UserService.set_username(str(user.id), "window_second")
        User.objects.filter(pk=user.pk).update(created_at=timezone.now() - timedelta(hours=49))
        updated = UserService.set_username(str(user.id), "window_third")
        assert updated.last_username_change is not None

    def test_released_handle_is_reserved_for_owner(self, create_user):
        owner = create_user(username="old_handle")
        other = create_user(email="other@example.com", username="other_handle")
        UserService.set_username(str(owner.id), "new_handle")

        assert not check_username_availability("OLD_HANDLE")["available"]
        assert check_username_availability("OLD_HANDLE", owner_id=owner.id)["available"]
        with pytest.raises(UserServiceError) as exc_info:
            UserService.set_username(str(other.id), "old_handle")
        assert exc_info.value.code == "USERNAME_TAKEN"

        reclaimed = UserService.set_username(str(owner.id), "OLD_HANDLE")
        assert reclaimed.username == "OLD_HANDLE"
        assert not UsernameReservation.objects.filter(username="old_handle").exists()

    def test_expired_reservation_can_be_claimed(self, create_user):
        owner = create_user(username="expired_old")
        other = create_user(email="newowner@example.com", username="other_name")
        UserService.set_username(str(owner.id), "expired_new")
        UsernameReservation.objects.filter(username="expired_old").update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        updated = UserService.set_username(str(other.id), "EXPIRED_OLD")
        assert updated.username == "EXPIRED_OLD"

    def test_signup_cannot_claim_released_handle(self, create_user):
        owner = create_user(username="reserved_signup")
        UserService.set_username(str(owner.id), "owner_new_name")
        with pytest.raises(AuthenticationError) as exc_info:
            AuthService.register(
                email="new-signup@example.com",
                password="Password123!",
                username="RESERVED_SIGNUP",
                date_of_birth="2000-01-01",
            )
        assert exc_info.value.code == "USERNAME_TAKEN"

    def test_permanent_deletion_reserves_released_handle(self, create_user):
        owner = create_user(username="deleted_handle")
        anonymize_user_for_permanent_delete(owner, timezone.now())
        assert not check_username_availability("deleted_handle")["available"]
        assert UsernameReservation.objects.filter(username="deleted_handle").exists()

    def test_case_variant_of_live_username_is_rejected(self, create_user):
        create_user(username="Unique_Handle")
        other = create_user(email="caseother@example.com", username="other_case")
        with pytest.raises(UserServiceError) as exc_info:
            UserService.set_username(str(other.id), "unique_handle")
        assert exc_info.value.code == "USERNAME_TAKEN"

    def test_database_rejects_case_variant_race(self, create_user):
        create_user(username="Case_Protected")
        with pytest.raises(IntegrityError), transaction.atomic():
            User.objects.create_user(email="case-race@example.com", username="case_protected")

    def test_emoji_not_allowed_in_handle(self):
        with pytest.raises(UsernameValidationError):
            validate_username_format("grace🙏")


class TestDateOfBirth:
    """Test DOB encryption and storage."""

    def test_set_dob_success(self, create_user):
        """Valid DOB should be encrypted and stored."""
        user = create_user()
        result = UserService.set_date_of_birth(str(user.id), "1995-06-15")
        assert result is True

    def test_get_dob_after_set(self, create_user):
        """Should decrypt back to original value."""
        user = create_user()
        UserService.set_date_of_birth(str(user.id), "1995-06-15")
        dob = UserService.get_date_of_birth(str(user.id))
        assert dob == "1995-06-15"

    def test_set_dob_under_13(self, create_user):
        """Users under 13 should be rejected."""
        user = create_user()
        with pytest.raises(UserServiceError) as exc_info:
            UserService.set_date_of_birth(str(user.id), "2020-01-01")
        assert exc_info.value.code == "AGE_REQUIREMENT_NOT_MET"

    def test_set_dob_invalid_format(self, create_user):
        """Invalid date format should fail."""
        user = create_user()
        with pytest.raises(UserServiceError) as exc_info:
            UserService.set_date_of_birth(str(user.id), "June 15, 1995")
        assert exc_info.value.code == "INVALID_DATE_FORMAT"

    def test_get_dob_not_set(self, create_user):
        """Should return None if DOB never set."""
        user = create_user()
        dob = UserService.get_date_of_birth(str(user.id))
        assert dob is None


@pytest.mark.django_db
class TestAccountDetailsGraphQL:
    def _auth_client(self, authenticated_user):
        client = Client()
        client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {authenticated_user['access_token']}"
        return client

    def test_account_details_query_returns_settings_card_data(self, authenticated_user):
        user = authenticated_user["user"]
        user.location = "Lagos, Nigeria"
        user.save(update_fields=["location", "updated_at"])

        query = """
        query AccountDetails {
          accountDetails {
            memberSince
            memberSinceDate
            location
            accountStatus
          }
        }
        """

        response = self._auth_client(authenticated_user).post(
            "/graphql/",
            data=json.dumps({"query": query}),
            content_type="application/json",
        )
        content = json.loads(response.content)
        assert "errors" not in content, content.get("errors")

        details = content["data"]["accountDetails"]
        assert details["memberSince"] == user.created_at.strftime("%B %Y")
        assert details["memberSinceDate"] == user.created_at.isoformat()
        assert details["location"] == "Lagos, Nigeria"
        assert details["accountStatus"] == "active"

    def test_update_account_details_updates_only_location(self, authenticated_user):
        user = authenticated_user["user"]

        mutation = """
        mutation UpdateAccountDetails($location: String!) {
          updateAccountDetails(location: $location) {
            success
            accountDetails {
              memberSince
              memberSinceDate
              location
              accountStatus
            }
            error { code message field }
          }
        }
        """

        response = self._auth_client(authenticated_user).post(
            "/graphql/",
            data=json.dumps(
                {
                    "query": mutation,
                    "variables": {"location": "Abuja, Nigeria"},
                }
            ),
            content_type="application/json",
        )
        content = json.loads(response.content)
        assert "errors" not in content, content.get("errors")

        payload = content["data"]["updateAccountDetails"]
        assert payload["success"] is True
        assert payload["accountDetails"]["location"] == "Abuja, Nigeria"
        assert payload["accountDetails"]["accountStatus"] == "active"

        user.refresh_from_db()
        assert user.location == "Abuja, Nigeria"

    def test_rest_me_includes_account_details(self, api_client, authenticated_user):
        user = authenticated_user["user"]
        user.location = "Lagos, Nigeria"
        user.bio_link = "https://ziona.app/welcome"
        user.save(update_fields=["location", "bio_link", "updated_at"])
        api_client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {authenticated_user['access_token']}"

        response = api_client.get("/api/auth/me")
        data = response.json()["data"]

        assert response.status_code == 200
        assert data["profile"]["bioLink"] == "https://ziona.app/welcome"
        assert data["accountDetails"]["memberSince"] == user.created_at.strftime("%B %Y")
        assert data["accountDetails"]["memberSinceDate"] == user.created_at.isoformat()
        assert data["accountDetails"]["location"] == "Lagos, Nigeria"
        assert data["accountDetails"]["accountStatus"] == "active"

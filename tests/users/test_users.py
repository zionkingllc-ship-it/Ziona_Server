"""Tests for user services — username and DOB."""

import pytest

from core.users.selectors import check_username_availability, suggest_usernames
from core.users.services import UserService, UserServiceError
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

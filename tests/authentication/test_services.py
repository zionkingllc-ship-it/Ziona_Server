import pytest

from core.authentication.services import AuthenticationError, AuthService


class TestRegistration:
    """Test user registration flow (email + password + username + DOB)."""

    def test_register_success(self, db):
        """Registration with all required fields returns user (no tokens)."""
        result = AuthService.register(
            email="newuser@example.com",
            password="SecureP@ss1",
            username="newuser2025",
            date_of_birth="2000-01-15",
        )

        assert result["user"].email == "newuser@example.com"
        assert result["user"].username == "newuser2025"
        assert "message" in result
        assert "access_token" not in result
        assert "refresh_token" not in result

    def test_register_duplicate_email(self, create_user):
        """Registration with existing verified email should fail."""
        create_user(email="taken@example.com", username="existing", is_email_verified=True)

        with pytest.raises(AuthenticationError) as exc_info:
            AuthService.register(
                email="taken@example.com",
                password="SecureP@ss1",
                username="newuser123",
                date_of_birth="2000-01-15",
            )
        assert exc_info.value.code == "EMAIL_ALREADY_REGISTERED"

    def test_register_duplicate_username(self, create_user):
        """Registration with taken username should fail."""
        create_user(email="existing@example.com", username="takenname")

        with pytest.raises(AuthenticationError) as exc_info:
            AuthService.register(
                email="new@example.com",
                password="SecureP@ss1",
                username="takenname",
                date_of_birth="2000-01-15",
            )
        assert exc_info.value.code == "USERNAME_TAKEN"

    def test_register_weak_password(self, db):
        """Registration with weak password should fail."""
        with pytest.raises(AuthenticationError) as exc_info:
            AuthService.register(
                email="user@example.com",
                password="weak",
                username="weakpwd",
                date_of_birth="2000-01-15",
            )
        assert "PASSWORD" in exc_info.value.code

    def test_register_underage(self, db):
        """Registration with age < 13 should fail."""
        with pytest.raises(AuthenticationError) as exc_info:
            AuthService.register(
                email="child@example.com",
                password="SecureP@ss1",
                username="childuser",
                date_of_birth="2020-01-15",
            )
        assert exc_info.value.code == "AGE_REQUIREMENT_NOT_MET"

    def test_register_invalid_username_format(self, db):
        """Registration with bad username format should fail."""
        with pytest.raises(AuthenticationError) as exc_info:
            AuthService.register(
                email="user@example.com",
                password="SecureP@ss1",
                username="ab",
                date_of_birth="2000-01-15",
            )
        assert exc_info.value.code == "USERNAME_LENGTH_INVALID"


class TestLogin:
    """Test user login flow."""

    def test_login_success(self, create_user):
        """Valid credentials should return tokens."""
        create_user(
            email="login@example.com",
            username="loginuser",
            password="SecureP@ss1",
            is_email_verified=True,
        )

        result = AuthService.login(
            email="login@example.com",
            password="SecureP@ss1",
        )

        assert result["user"].email == "login@example.com"
        assert "access_token" in result
        assert "refresh_token" in result

    def test_login_wrong_password(self, create_user):
        """Wrong password should fail."""
        create_user(
            email="user@example.com",
            username="user1",
            password="CorrectP@ss1",
        )

        with pytest.raises(AuthenticationError) as exc_info:
            AuthService.login(
                email="user@example.com",
                password="WrongP@ss1!",
            )
        assert exc_info.value.code == "INVALID_CREDENTIALS"

    def test_login_nonexistent_email(self, db):
        """Login with non-existent email should fail."""
        with pytest.raises(AuthenticationError) as exc_info:
            AuthService.login(
                email="nobody@example.com",
                password="SomeP@ss1!",
            )
        assert exc_info.value.code == "INVALID_CREDENTIALS"

    def test_login_unverified_email(self, create_user):
        """Login with unverified email sends OTP instead of raising."""
        create_user(
            email="unverified@example.com",
            username="unverified",
            password="SecureP@ss1",
            is_email_verified=False,
        )

        result = AuthService.login(
            email="unverified@example.com",
            password="SecureP@ss1",
        )
        assert result["requires_verification"] is True
        assert "access_token" not in result


class TestLogout:
    """Test user logout flow."""

    def test_logout_success(self, authenticated_user):
        """Logout should succeed and revoke tokens."""
        result = AuthService.logout(
            access_token=authenticated_user["access_token"],
            refresh_token=authenticated_user["refresh_token"],
            user_id=str(authenticated_user["user"].id),
        )
        assert result is True


class TestPasswordValidation:
    """Test password validation per Figma: 8-20 chars, 1 letter, 1 number, 1 special."""

    @pytest.mark.parametrize(
        "password,expected_code",
        [
            ("Sh0r!!", "PASSWORD_LENGTH_INVALID"),
            ("Abcdefghij1234567890!", "PASSWORD_LENGTH_INVALID"),
            ("12345678!", "PASSWORD_NO_LETTER"),
            ("abcdefgh!", "PASSWORD_NO_NUMBER"),
            ("Abcdefgh1", "PASSWORD_NO_SPECIAL"),
        ],
    )
    def test_password_complexity(self, db, password, expected_code):
        """Passwords not meeting Figma requirements should fail."""
        with pytest.raises(AuthenticationError) as exc_info:
            AuthService.register(
                email="test@test.com",
                password=password,
                username="testuser99",
                date_of_birth="2000-01-15",
            )
        assert exc_info.value.code == expected_code

    def test_password_valid_lowercase_only(self, db):
        """Password with lowercase letters (no uppercase) should pass."""
        result = AuthService.register(
            email="loweronly@test.com",
            password="abcdefg1!",
            username="loweronly",
            date_of_birth="2000-01-15",
        )
        assert result["user"].email == "loweronly@test.com"

    def test_password_at_boundaries(self, db):
        """Passwords at exactly 8 and 20 chars should pass."""
        result8 = AuthService.register(
            email="min@test.com",
            password="aBcde1!x",
            username="minpwd",
            date_of_birth="2000-01-15",
        )
        assert result8["user"] is not None

        result20 = AuthService.register(
            email="max@test.com",
            password="aBcdefghij12345678!x",
            username="maxpwd",
            date_of_birth="2000-01-15",
        )
        assert result20["user"] is not None


class TestUsernameSuggestions:
    """Test username suggestion algorithm."""

    def test_suggest_returns_four(self, db):
        """Should return exactly 4 unique suggestions."""
        suggestions = AuthService.suggest_usernames(
            email="john@example.com",
            date_of_birth="1995-08-12",
        )
        assert len(suggestions) == 4
        assert len(set(suggestions)) == 4

    def test_suggest_format(self, db):
        """Suggestions should be valid username format."""
        suggestions = AuthService.suggest_usernames(
            email="john.doe@gmail.com",
            date_of_birth="1995-08-12",
        )
        for s in suggestions:
            assert 3 <= len(s) <= 30
            assert s.replace("_", "").isalnum()

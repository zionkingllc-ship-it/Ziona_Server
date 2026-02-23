import pytest

from core.authentication.services import AuthService, AuthenticationError


class TestRegistration:
    """Test user registration flow (Figma: email → password only)."""

    def test_register_success(self, db):
        """Registration with email+password returns user and tokens."""
        result = AuthService.register(
            email="newuser@example.com",
            password="SecureP@ss1",
        )

        assert result["user"].email == "newuser@example.com"
        
        assert result["user"].username.startswith("user_")
        assert "access_token" in result
        assert "refresh_token" in result

    def test_register_with_full_name(self, db):
        """Registration can optionally include full_name."""
        result = AuthService.register(
            email="named@example.com",
            password="SecureP@ss1",
            full_name="Full Name",
        )
        assert result["user"].full_name == "Full Name"

    def test_register_duplicate_email(self, create_user):
        """Registration with existing email should fail."""
        create_user(email="taken@example.com", username="existing")

        with pytest.raises(AuthenticationError) as exc_info:
            AuthService.register(
                email="taken@example.com",
                password="SecureP@ss1",
            )
        assert exc_info.value.code == "EMAIL_EXISTS"

    def test_register_weak_password(self, db):
        """Registration with weak password should fail."""
        with pytest.raises(AuthenticationError) as exc_info:
            AuthService.register(
                email="user@example.com",
                password="weak",
            )
        assert "PASSWORD" in exc_info.value.code


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
        """Login with unverified email should fail."""
        create_user(
            email="unverified@example.com",
            username="unverified",
            password="SecureP@ss1",
            is_email_verified=False,
        )

        with pytest.raises(AuthenticationError) as exc_info:
            AuthService.login(
                email="unverified@example.com",
                password="SecureP@ss1",
            )
        assert exc_info.value.code == "EMAIL_NOT_VERIFIED"


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
            )
        assert exc_info.value.code == expected_code

    def test_password_valid_lowercase_only(self, db):
        """Password with lowercase letters (no uppercase) should pass."""
        
        result = AuthService.register(
            email="loweronly@test.com",
            password="abcdefg1!",
        )
        assert result["user"].email == "loweronly@test.com"

    def test_password_at_boundaries(self, db):
        """Passwords at exactly 8 and 20 chars should pass."""
        
        result8 = AuthService.register(
            email="min@test.com",
            password="aBcde1!x",
        )
        assert result8["user"] is not None

        result20 = AuthService.register(
            email="max@test.com",
            password="aBcdefghij12345678!x",
        )
        assert result20["user"] is not None

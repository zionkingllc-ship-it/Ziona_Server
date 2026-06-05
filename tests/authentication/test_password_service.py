"""Tests for PasswordService — add password and change password."""

from unittest.mock import patch

import pytest

from core.authentication.password_service import PasswordService
from core.authentication.validators import AuthenticationError


@pytest.fixture
def email_user(create_user):
    """User who signed up with email+password."""
    return create_user(
        email="email@test.com",
        username="emailuser",
        password="OldPass123!",
    )


@pytest.fixture
def oauth_user(db):
    """User who signed up via Google OAuth (no usable password)."""
    from core.users.models import User

    user = User.objects.create(
        email="oauth@test.com",
        username="oauthuser",
        auth_provider="google",
        is_email_verified=True,
    )
    user.set_unusable_password()
    user.save()
    return user


class TestAddPassword:
    """Tests for PasswordService.add_password."""

    def test_oauth_user_can_add_password(self, oauth_user):
        result = PasswordService.add_password(str(oauth_user.id), "NewPass123!")
        assert "user" in result

        oauth_user.refresh_from_db()
        assert oauth_user.has_usable_password() is True

    def test_user_with_password_cannot_add_again(self, email_user):
        with pytest.raises(AuthenticationError) as exc_info:
            PasswordService.add_password(str(email_user.id), "Another123!")
        assert exc_info.value.code == "PASSWORD_ALREADY_EXISTS"

    def test_weak_password_fails(self, oauth_user):
        with pytest.raises(AuthenticationError) as exc_info:
            PasswordService.add_password(str(oauth_user.id), "short")
        assert exc_info.value.code == "PASSWORD_LENGTH_INVALID"

    def test_can_login_with_added_password(self, oauth_user):
        """After adding password, user can authenticate with it."""
        PasswordService.add_password(str(oauth_user.id), "MyNewPass123!")

        oauth_user.refresh_from_db()
        assert oauth_user.check_password("MyNewPass123!") is True

    def test_has_usable_password_true_after(self, oauth_user):
        assert oauth_user.has_usable_password() is False
        PasswordService.add_password(str(oauth_user.id), "Secure123!")
        oauth_user.refresh_from_db()
        assert oauth_user.has_usable_password() is True


class TestChangePassword:
    """Tests for PasswordService.change_password."""

    def test_change_password_success(self, email_user):
        result = PasswordService.change_password(
            user_id=str(email_user.id),
            current_password="OldPass123!",
            new_password="NewPass456!",
        )
        assert result["message"] == "Password changed successfully."
        assert result["signed_out_devices"] == 0

        email_user.refresh_from_db()
        assert email_user.check_password("NewPass456!") is True

    def test_wrong_current_password_fails(self, email_user):
        with pytest.raises(AuthenticationError) as exc_info:
            PasswordService.change_password(
                user_id=str(email_user.id),
                current_password="WrongPass!",
                new_password="NewPass456!",
            )
        assert exc_info.value.code == "CURRENT_PASSWORD_INCORRECT"

    def test_weak_new_password_fails(self, email_user):
        with pytest.raises(AuthenticationError) as exc_info:
            PasswordService.change_password(
                user_id=str(email_user.id),
                current_password="OldPass123!",
                new_password="weak",
            )
        assert exc_info.value.code == "PASSWORD_LENGTH_INVALID"

    @patch("core.authentication.password_service.TokenService")
    def test_sign_out_other_devices(self, mock_token_service, email_user):
        """Signing out other devices calls revoke_all_user_tokens_except."""
        mock_token_service.revoke_all_user_tokens_except.return_value = 3

        result = PasswordService.change_password(
            user_id=str(email_user.id),
            current_password="OldPass123!",
            new_password="NewPass456!",
            sign_out_other_devices=True,
            current_jti="keep-this-jti",
        )
        assert result["signed_out_devices"] == 3
        mock_token_service.revoke_all_user_tokens_except.assert_called_once_with(
            user_id=str(email_user.id),
            keep_jti="keep-this-jti",
        )

    @patch("core.authentication.password_service.TokenService")
    def test_signout_false_keeps_sessions(self, mock_token_service, email_user):
        """Without sign_out_other_devices, no tokens are revoked."""
        result = PasswordService.change_password(
            user_id=str(email_user.id),
            current_password="OldPass123!",
            new_password="NewPass456!",
            sign_out_other_devices=False,
        )
        assert result["signed_out_devices"] == 0
        mock_token_service.revoke_all_user_tokens_except.assert_not_called()

    @patch("core.authentication.password_service.log_security_event")
    def test_password_change_logs_event(self, mock_log, email_user):
        """Password change logs a security event."""
        PasswordService.change_password(
            user_id=str(email_user.id),
            current_password="OldPass123!",
            new_password="NewPass456!",
        )
        mock_log.assert_called_once()
        assert mock_log.call_args[0][0] == "auth.password_changed"


class TestOAuthToPasswordFlow:
    """Integration test: OAuth user adds password then uses it."""

    def test_full_flow(self, oauth_user):
        assert oauth_user.has_usable_password() is False

        PasswordService.add_password(str(oauth_user.id), "FirstPass123!")
        oauth_user.refresh_from_db()
        assert oauth_user.has_usable_password() is True
        assert oauth_user.check_password("FirstPass123!") is True

        result = PasswordService.change_password(
            user_id=str(oauth_user.id),
            current_password="FirstPass123!",
            new_password="SecondPass456!",
        )
        assert result["message"] == "Password changed successfully."

        oauth_user.refresh_from_db()
        assert oauth_user.check_password("SecondPass456!") is True
        assert oauth_user.check_password("FirstPass123!") is False


class TestPasswordResetRequest:
    """Tests for password reset OTP request behavior."""

    def test_existing_user_fails_when_reset_email_cannot_queue(self, email_user):
        with (
            patch("core.emails.services.EmailService.send_reset_password", return_value=False),
            pytest.raises(AuthenticationError) as exc_info,
        ):
            PasswordService.request_password_reset("email@test.com")

        assert exc_info.value.code == "OTP_EMAIL_QUEUE_FAILED"

    def test_unknown_user_still_returns_success_to_prevent_enumeration(self, db):
        with patch("core.emails.services.EmailService.send_reset_password") as mock_send:
            result = PasswordService.request_password_reset("missing@test.com")

        assert result is True
        mock_send.assert_not_called()

import json
from unittest.mock import patch

import pytest
from django.conf import settings
from django.urls import reverse

from core.users.models import User


@pytest.fixture
def mock_google_verify():
    with patch("google.oauth2.id_token.verify_oauth2_token") as mock:
        yield mock


@pytest.fixture(autouse=True)
def configure_google_client_ids(settings):
    client_id = "test-web-client.apps.googleusercontent.com"
    settings.GOOGLE_CLIENT_ID = client_id
    settings.GOOGLE_CLIENT_IDS = [client_id]


@pytest.mark.django_db
class TestGoogleOAuth:
    """Test suite for the 5 explicit Google OAuth scenarios."""

    url = reverse("authentication:google-oauth")

    def test_new_google_user(self, api_client, mock_google_verify):
        """Scenario 1: New Google User."""
        settings.GOOGLE_CLIENT_IDS = [settings.GOOGLE_CLIENT_ID]
        mock_google_verify.return_value = {
            "aud": settings.GOOGLE_CLIENT_ID,
            "email": "new.user@google.com",
            "sub": "google_id_12345",
            "email_verified": True,
            "name": "New User",
            "picture": "http://example.com/pic.jpg",
        }

        response = api_client.post(
            self.url,
            data=json.dumps({"id_token": "valid_mock_token"}),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["isNewUser"] is True

        user_data = data["data"]["user"]
        assert user_data["email"] == "new.user@google.com"
        assert user_data["isEmailVerified"] is True
        assert user_data["needsUsernameSelection"] is True

        assert "accessToken" in data["data"]["tokens"]
        mock_google_verify.assert_called_once()
        assert mock_google_verify.call_args.args[2] is None

        # Track Provider Validation
        user = User.objects.get(email="new.user@google.com")
        assert user.social_auth_provider == "google"
        assert user.google_id == "google_id_12345"
        assert not user.has_usable_password()

    def test_accepts_google_token_from_configured_mobile_client_id(
        self, api_client, mock_google_verify, settings
    ):
        """Valid first-party mobile audiences should be accepted."""
        settings.GOOGLE_CLIENT_IDS = [
            "web-client-id.apps.googleusercontent.com",
            "ios-client-id.apps.googleusercontent.com",
        ]
        mock_google_verify.return_value = {
            "aud": "ios-client-id.apps.googleusercontent.com",
            "email": "ios.user@google.com",
            "sub": "google_ios_12345",
            "email_verified": True,
            "name": "iOS User",
        }

        response = api_client.post(
            self.url,
            data=json.dumps({"id_token": "valid_ios_token"}),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["user"]["email"] == "ios.user@google.com"

    def test_rejects_google_token_from_unconfigured_audience(
        self, api_client, mock_google_verify, settings
    ):
        """A valid Google token from an unknown client ID must be rejected."""
        settings.GOOGLE_CLIENT_IDS = ["web-client-id.apps.googleusercontent.com"]
        mock_google_verify.return_value = {
            "aud": "untrusted-client-id.apps.googleusercontent.com",
            "email": "bad.audience@google.com",
            "sub": "google_bad_audience",
            "email_verified": True,
        }

        response = api_client.post(
            self.url,
            data=json.dumps({"id_token": "valid_wrong_audience_token"}),
            content_type="application/json",
        )

        assert response.status_code == 400
        data = response.json()
        assert data["success"] is False
        assert data["error"]["code"] == "INVALID_OAUTH_TOKEN"
        assert data["error"]["message"] == "Invalid Google token audience"

    def test_existing_google_user_login(self, api_client, mock_google_verify):
        """Scenario 2: Existing Google User Login."""
        # Create an existing Google user
        user = User.objects.create_user(
            email="existing@google.com",
            username="existing_user",
            social_auth_provider="google",
            google_id="existing_id_999",
            is_email_verified=True,
        )
        user.set_unusable_password()
        user.save()

        mock_google_verify.return_value = {
            "aud": settings.GOOGLE_CLIENT_ID,
            "email": "existing@google.com",
            "sub": "existing_id_999",
            "email_verified": True,
        }

        response = api_client.post(
            self.url,
            data=json.dumps({"id_token": "valid_mock_token_existing"}),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["isNewUser"] is False
        assert data["data"]["user"]["needsUsernameSelection"] is False

    def test_password_account_google_oauth_auto_links_when_email_verified(
        self, api_client, mock_google_verify
    ):
        """Verified Google email on an existing password account auto-links (dual login)."""
        User.objects.create_user(
            email="conflict@gmail.com",
            username="conflict_user",
            password="StrongPassword123!",  # pragma: allowlist secret
            social_auth_provider=None,  # Standard registration
            is_email_verified=True,
        )

        mock_google_verify.return_value = {
            "aud": settings.GOOGLE_CLIENT_ID,
            "email": "conflict@gmail.com",
            "sub": "linked_google_id",
            "email_verified": True,
        }

        response = api_client.post(
            self.url,
            data=json.dumps({"id_token": "valid_mock_token_conflict"}),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["isNewUser"] is False

        user = User.objects.get(email="conflict@gmail.com")
        assert user.google_id == "linked_google_id"
        # Password preserved — the account now supports both password and Google.
        assert user.has_usable_password() is True

    def test_password_account_google_oauth_blocked_when_email_unverified(
        self, api_client, mock_google_verify
    ):
        """An UNVERIFIED Google email must not take over an existing password account."""
        User.objects.create_user(
            email="secure@gmail.com",
            username="secure_user",
            password="StrongPassword123!",  # pragma: allowlist secret
            social_auth_provider=None,
            is_email_verified=True,
        )

        mock_google_verify.return_value = {
            "aud": settings.GOOGLE_CLIENT_ID,
            "email": "secure@gmail.com",
            "sub": "attacker_google_id",
            "email_verified": False,
        }

        response = api_client.post(
            self.url,
            data=json.dumps({"id_token": "unverified_mock_token"}),
            content_type="application/json",
        )

        assert response.status_code == 400
        data = response.json()
        assert data["success"] is False
        assert data["error"]["code"] == "EMAIL_REGISTERED_WITH_PASSWORD"
        assert User.objects.get(email="secure@gmail.com").google_id is None

    def test_unverified_password_signup_can_continue_with_google_oauth(
        self, api_client, mock_google_verify
    ):
        """Unverified email/password signups can be safely continued with Google."""
        User.objects.create_user(
            email="pending@gmail.com",
            username="pending_user",
            password="StrongPassword123!",  # pragma: allowlist secret
            social_auth_provider=None,
            is_email_verified=False,
        )

        check_email_response = api_client.post(
            reverse("authentication:check-email"),
            data=json.dumps({"email": "pending@gmail.com"}),
            content_type="application/json",
        )
        assert check_email_response.status_code == 200
        assert check_email_response.json()["data"]["exists"] is False

        mock_google_verify.return_value = {
            "aud": settings.GOOGLE_CLIENT_ID,
            "email": "pending@gmail.com",
            "sub": "pending_google_id",
            "email_verified": True,
            "name": "Pending Google User",
            "picture": "http://example.com/pending.jpg",
        }

        response = api_client.post(
            self.url,
            data=json.dumps({"id_token": "valid_mock_token_pending"}),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["isNewUser"] is False

        user = User.objects.get(email="pending@gmail.com")
        assert user.google_id == "pending_google_id"
        assert user.is_email_verified is True
        # The signup password was never proven to own this inbox, so it is
        # dropped; Google is now the only way in until Forgot Password.
        assert user.has_usable_password() is False
        assert user.social_auth_provider == "google"
        assert user.full_name == "Pending Google User"
        assert user.avatar_url == "http://example.com/pending.jpg"

    def test_invalid_google_token(self, api_client, mock_google_verify):
        """Scenario 4: Invalid Google Token."""
        # Raise value error indicating token fails verification
        mock_google_verify.side_effect = ValueError("Wrong Token")

        response = api_client.post(
            self.url,
            data=json.dumps({"id_token": "invalid_trash_token"}),
            content_type="application/json",
        )

        assert response.status_code == 400
        data = response.json()
        assert data["success"] is False
        assert data["error"]["code"] == "INVALID_OAUTH_TOKEN"
        assert data["error"]["message"] == "Invalid Google authentication token"

    def test_google_token_different_provider(self, api_client, mock_google_verify):
        """Scenario X: Account already registered with Facebook"""
        User.objects.create_user(
            email="facebook@gmail.com",
            username="fb_user",
            social_auth_provider="facebook",
        )

        mock_google_verify.return_value = {
            "aud": settings.GOOGLE_CLIENT_ID,
            "email": "facebook@gmail.com",
            "sub": "google_id_222",
            "email_verified": True,
        }

        response = api_client.post(
            self.url,
            data=json.dumps({"id_token": "valid_mock_token_fb"}),
            content_type="application/json",
        )

        assert response.status_code == 400
        data = response.json()
        assert data["success"] is False
        assert data["error"]["code"] == "EMAIL_REGISTERED_WITH_DIFFERENT_PROVIDER"
        assert "facebook instead" in data["error"]["message"]

    def test_google_token_for_different_email(self, api_client, mock_google_verify):
        """Scenario 5: Google Token for Different Email isolates correctly."""
        # Start with an isolated base Google user.
        User.objects.create_user(
            email="userA@google.com",
            username="userA",
            social_auth_provider="google",
            google_id="google_id_A",
        )

        mock_google_verify.return_value = {
            "aud": settings.GOOGLE_CLIENT_ID,
            "email": "userB@google.com",
            "sub": "google_id_B",
            "email_verified": True,
        }

        response = api_client.post(
            self.url,
            data=json.dumps({"id_token": "valid_mock_token_B"}),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

        users = User.objects.filter(social_auth_provider="google")
        assert users.count() == 2

        user_b = User.objects.get(email="userB@google.com")
        assert user_b.google_id == "google_id_B"


def _apple_registered_user(email: str, *, apple_sub: str = "apple_sub_1") -> User:
    """An account created by Sign in with Apple: no usable password."""
    user = User.objects.create_user(
        email=email,
        username=f"apple_{apple_sub}",
        auth_provider="apple",
        social_auth_provider="apple",
        apple_sub=apple_sub,
        is_email_verified=True,
    )
    user.set_unusable_password()
    user.save(update_fields=["password"])
    return user


@pytest.mark.django_db
class TestGoogleLinksAppleAccount:
    """A user who registered with Apple can later sign in with Google."""

    url = reverse("authentication:google-oauth")

    def _sign_in(self, api_client, mock_google_verify, *, email, sub, verified=True):
        mock_google_verify.return_value = {
            "aud": settings.GOOGLE_CLIENT_ID,
            "email": email,
            "sub": sub,
            "email_verified": verified,
        }
        return api_client.post(
            self.url,
            data=json.dumps({"id_token": "mock_token"}),
            content_type="application/json",
        )

    def test_verified_google_email_links_to_apple_account(self, api_client, mock_google_verify):
        apple_user = _apple_registered_user("both@example.com")

        response = self._sign_in(
            api_client, mock_google_verify, email="both@example.com", sub="google_both"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["isNewUser"] is False
        assert User.objects.filter(email="both@example.com").count() == 1

        user = User.objects.get(pk=apple_user.pk)
        assert user.google_id == "google_both"
        assert user.apple_sub == "apple_sub_1"
        # The sign-up provider is history, not "last used" — it must not flip.
        assert user.social_auth_provider == "apple"
        assert user.auth_provider == "apple"

    def test_provider_does_not_flip_on_later_google_sign_ins(self, api_client, mock_google_verify):
        apple_user = _apple_registered_user("repeat@example.com")

        for _ in range(2):
            response = self._sign_in(
                api_client, mock_google_verify, email="repeat@example.com", sub="google_repeat"
            )
            assert response.status_code == 200

        user = User.objects.get(pk=apple_user.pk)
        assert user.social_auth_provider == "apple"
        assert user.auth_provider == "apple"

    def test_unverified_google_email_cannot_join_apple_account(
        self, api_client, mock_google_verify
    ):
        apple_user = _apple_registered_user("victim@example.com")

        response = self._sign_in(
            api_client,
            mock_google_verify,
            email="victim@example.com",
            sub="attacker_google",
            verified=False,
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "EMAIL_REGISTERED_WITH_DIFFERENT_PROVIDER"
        assert User.objects.get(pk=apple_user.pk).google_id is None

    def test_google_account_mismatch_still_enforced_on_linked_apple_account(
        self, api_client, mock_google_verify
    ):
        apple_user = _apple_registered_user("linked@example.com")
        User.objects.filter(pk=apple_user.pk).update(google_id="google_original")

        response = self._sign_in(
            api_client, mock_google_verify, email="linked@example.com", sub="google_other"
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "GOOGLE_ACCOUNT_MISMATCH"
        assert User.objects.get(pk=apple_user.pk).google_id == "google_original"

    def test_private_relay_apple_account_is_not_matched(self, api_client, mock_google_verify):
        """Hide My Email addresses never equal the Google email — a new account results."""
        relay_user = _apple_registered_user("x7k2@privaterelay.appleid.com")

        response = self._sign_in(
            api_client, mock_google_verify, email="real.person@gmail.com", sub="google_real"
        )

        assert response.status_code == 200
        assert response.json()["data"]["isNewUser"] is True
        assert User.objects.get(pk=relay_user.pk).google_id is None

    def test_unverified_google_cannot_join_unverified_password_account(
        self, api_client, mock_google_verify
    ):
        """Neither side has proven the inbox, so nothing links.

        The old rule refused only when the password account was verified, so
        unverified + unverified slipped through and linked.
        """
        password_user = User.objects.create_user(
            email="nobody.verified@example.com",
            username="nobody_verified",
            password="StrongPassword123!",  # pragma: allowlist secret
            is_email_verified=False,
        )

        response = self._sign_in(
            api_client,
            mock_google_verify,
            email="nobody.verified@example.com",
            sub="unverified_google",
            verified=False,
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "EMAIL_REGISTERED_WITH_PASSWORD"
        assert User.objects.get(pk=password_user.pk).google_id is None

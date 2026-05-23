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

    def test_email_password_account_tries_google_oauth(self, api_client, mock_google_verify):
        """Scenario 3: Email/Password Account Tries Google OAuth."""
        # Create an existing email/password user
        User.objects.create_user(
            email="conflict@gmail.com",
            username="conflict_user",
            password="StrongPassword123!",
            social_auth_provider=None,  # Standard registration
        )

        mock_google_verify.return_value = {
            "aud": settings.GOOGLE_CLIENT_ID,
            "email": "conflict@gmail.com",
            "sub": "different_google_id",
            "email_verified": True,
        }

        response = api_client.post(
            self.url,
            data=json.dumps({"id_token": "valid_mock_token_conflict"}),
            content_type="application/json",
        )

        assert response.status_code == 400
        data = response.json()
        assert data["success"] is False
        assert data["error"]["code"] == "EMAIL_REGISTERED_WITH_PASSWORD"
        assert "sign in with your password instead" in data["error"]["message"]

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

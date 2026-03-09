import json

from django.test import Client


class TestRegisterEndpoint:
    """Test POST /api/auth/register — standardized camelCase responses."""

    def test_register_new_user_201(self, api_client: Client, db):
        """New registration returns 201 with user and requiresVerification."""
        response = api_client.post(
            "/api/auth/register",
            data=json.dumps(
                {
                    "email": "new@example.com",
                    "password": "SecureP@ss1",
                    "username": "newuser2025",
                    "date_of_birth": "2000-01-15",
                }
            ),
            content_type="application/json",
        )

        assert response.status_code == 201
        data = response.json()
        assert data["success"] is True
        user = data["data"]["user"]
        assert user["email"] == "new@example.com"
        assert user["username"] == "newuser2025"
        assert user["isEmailVerified"] is False
        assert data["data"]["requiresVerification"] is True
        assert "tokens" not in data["data"]

    def test_register_unverified_email_updates_200(self, api_client: Client, create_user):
        """Re-register with unverified email updates user and returns 200."""
        create_user(
            email="retry@example.com",
            username="oldname",
            is_email_verified=False,
        )

        response = api_client.post(
            "/api/auth/register",
            data=json.dumps(
                {
                    "email": "retry@example.com",
                    "password": "SecureP@ss1",
                    "username": "newname",
                    "date_of_birth": "2000-01-15",
                }
            ),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["user"]["username"] == "newname"

    def test_register_verified_email_error(self, api_client: Client, create_user):
        """Register with verified email returns 400 EMAIL_ALREADY_REGISTERED."""
        create_user(
            email="taken@example.com",
            username="existing",
            is_email_verified=True,
        )

        response = api_client.post(
            "/api/auth/register",
            data=json.dumps(
                {
                    "email": "taken@example.com",
                    "password": "SecureP@ss1",
                    "username": "newuser",
                    "date_of_birth": "2000-01-15",
                }
            ),
            content_type="application/json",
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "EMAIL_ALREADY_REGISTERED"

    def test_register_missing_fields(self, api_client: Client, db):
        """Registration with missing fields returns 400 MISSING_FIELDS."""
        response = api_client.post(
            "/api/auth/register",
            data=json.dumps({"email": "new@example.com"}),
            content_type="application/json",
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "MISSING_FIELDS"


class TestLoginEndpoint:
    """Test POST /api/auth/login — camelCase tokens."""

    def test_login_verified_returns_tokens(self, api_client: Client, create_user):
        """Verified user login returns tokens nested under data.tokens."""
        create_user(
            email="login@example.com",
            username="logintest",
            password="SecureP@ss1",
            is_email_verified=True,
        )

        response = api_client.post(
            "/api/auth/login",
            data=json.dumps({"email": "login@example.com", "password": "SecureP@ss1"}),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "accessToken" in data["data"]["tokens"]
        assert "refreshToken" in data["data"]["tokens"]
        assert data["data"]["user"]["isEmailVerified"] is True

    def test_login_unverified_sends_otp(self, api_client: Client, create_user):
        """Unverified user login returns requiresVerification, no tokens."""
        create_user(
            email="unverified@example.com",
            username="notyetverified",
            password="SecureP@ss1",
            is_email_verified=False,
        )

        response = api_client.post(
            "/api/auth/login",
            data=json.dumps({"email": "unverified@example.com", "password": "SecureP@ss1"}),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["requiresVerification"] is True
        assert "tokens" not in data["data"]

    def test_login_invalid_credentials(self, api_client: Client, create_user):
        """Invalid credentials return 401 INVALID_CREDENTIALS."""
        create_user(
            email="user@example.com",
            username="user1",
            password="SecureP@ss1",
        )

        response = api_client.post(
            "/api/auth/login",
            data=json.dumps({"email": "user@example.com", "password": "WrongP@ss!"}),
            content_type="application/json",
        )

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"


class TestTokenRefreshEndpoint:
    """Test POST /api/auth/refresh — tokens in camelCase."""

    def test_refresh_returns_new_tokens(self, api_client: Client, authenticated_user):
        """Valid refresh returns new token pair nested under tokens."""
        response = api_client.post(
            "/api/auth/refresh",
            data=json.dumps({"refresh_token": authenticated_user["refresh_token"]}),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "accessToken" in data["data"]["tokens"]
        assert "refreshToken" in data["data"]["tokens"]

    def test_refresh_invalid_token(self, api_client: Client, db):
        """Invalid refresh token returns 401."""
        response = api_client.post(
            "/api/auth/refresh",
            data=json.dumps({"refresh_token": "invalid.token"}),
            content_type="application/json",
        )

        assert response.status_code == 401


class TestLogoutEndpoint:
    """Test POST /api/auth/logout."""

    def test_logout_returns_200(self, api_client: Client, authenticated_user):
        """Logout with valid token returns 200 success."""
        response = api_client.post(
            "/api/auth/logout",
            data=json.dumps({"refresh_token": authenticated_user["refresh_token"]}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {authenticated_user['access_token']}",
        )

        assert response.status_code == 200
        assert response.json()["success"] is True


class TestSuggestUsernamesEndpoint:
    """Test POST /api/auth/suggest-usernames."""

    def test_suggest_returns_suggestions(self, api_client: Client, db):
        """Returns 4 unique suggestions."""
        response = api_client.post(
            "/api/auth/suggest-usernames",
            data=json.dumps({"email": "john@example.com", "date_of_birth": "1995-08-12"}),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert len(data["data"]["suggestions"]) == 4

    def test_suggest_missing_fields(self, api_client: Client, db):
        """Missing fields returns 400."""
        response = api_client.post(
            "/api/auth/suggest-usernames",
            data=json.dumps({"date_of_birth": "1990-01-01"}),
            content_type="application/json",
        )

        assert response.status_code == 400


class TestResendOTPEndpoint:
    """Test POST /api/auth/resend-otp — returns expiresIn."""

    def test_resend_returns_expires(self, api_client: Client, create_user):
        """Resend for unverified user returns expiresIn."""
        create_user(
            email="resend_view@example.com",
            username="resenduser",
            is_email_verified=False,
        )

        from django_redis import get_redis_connection

        redis_conn = get_redis_connection("default")
        redis_conn.delete("otp_resend:verify:resend_view@example.com")

        response = api_client.post(
            "/api/auth/resend-otp",
            data=json.dumps({"email": "resend_view@example.com"}),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["expiresIn"] == 600


class TestStandardizedResponseFormat:
    """Test that all responses follow the standard format."""

    def test_success_has_correct_shape(self, api_client: Client, db):
        """Success responses have {success: true, data: {...}}."""
        response = api_client.post(
            "/api/auth/suggest-usernames",
            data=json.dumps({"email": "test@test.com", "date_of_birth": "1995-01-01"}),
            content_type="application/json",
        )

        body = response.json()
        assert "success" in body
        assert body["success"] is True
        assert "data" in body

    def test_error_has_correct_shape(self, api_client: Client, db):
        """Error responses have {success: false, error: {message, code}}."""
        response = api_client.post(
            "/api/auth/register",
            data=json.dumps({"email": "only"}),
            content_type="application/json",
        )

        body = response.json()
        assert body["success"] is False
        assert "error" in body
        assert "message" in body["error"]
        assert "code" in body["error"]


class TestDeleteAccountEndpoint:
    """Test DELETE /api/auth/me."""

    def test_delete_account_success(self, api_client: Client, authenticated_user):
        """Authenticated DELETE removes the user."""
        from core.users.models import User

        user_id = authenticated_user["user"].id

        assert User.objects.filter(id=user_id).exists()

        response = api_client.delete(
            "/api/auth/me",
            HTTP_AUTHORIZATION=f"Bearer {authenticated_user['access_token']}",
        )

        assert response.status_code == 200
        assert response.json()["success"] is True
        assert "permanently deleted" in response.json()["data"]["message"]

        assert not User.objects.filter(id=user_id).exists()

    def test_delete_account_unauthorized(self, api_client: Client):
        """Unauthenticated DELETE returns 401."""
        response = api_client.delete("/api/auth/me")
        assert response.status_code == 401

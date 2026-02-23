import pytest
from django.test import Client


class TestRegisterEndpoint:
    """Test POST /api/auth/register (no username required)."""

    def test_register_returns_201(self, api_client: Client, db):
        """Valid registration with email+password should return 201."""
        response = api_client.post(
            "/api/auth/register",
            data=json.dumps({
                "email": "new@example.com",
                "password": "SecureP@ss1",
                "full_name": "Test User",
            }),
            content_type="application/json",
        )

        assert response.status_code == 201
        data = response.json()
        assert data["success"] is True
        assert "access_token" in data["data"]
        assert "refresh_token" in data["data"]
        assert data["data"]["user"]["email"] == "new@example.com"
        assert data["data"]["user"]["username"].startswith("user_")

    def test_register_missing_fields(self, api_client: Client, db):
        """Registration with missing password should return 400."""
        response = api_client.post(
            "/api/auth/register",
            data=json.dumps({"email": "new@example.com"}),
            content_type="application/json",
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "MISSING_FIELDS"


class TestLoginEndpoint:
    """Test POST /api/auth/login."""

    def test_login_returns_200(self, api_client: Client, create_user):
        """Valid login should return 200 with tokens."""
        create_user(
            email="login@example.com",
            username="logintest",
            password="SecureP@ss1",
        )

        response = api_client.post(
            "/api/auth/login",
            data=json.dumps({
                "email": "login@example.com",
                "password": "SecureP@ss1",
            }),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "access_token" in data["data"]

    def test_login_invalid_credentials(self, api_client: Client, create_user):
        """Invalid credentials should return 401."""
        create_user(
            email="user@example.com",
            username="user1",
            password="SecureP@ss1",
        )

        response = api_client.post(
            "/api/auth/login",
            data=json.dumps({
                "email": "user@example.com",
                "password": "WrongP@ss!",
            }),
            content_type="application/json",
        )

        assert response.status_code == 401


class TestTokenRefreshEndpoint:
    """Test POST /api/auth/refresh."""

    def test_refresh_returns_new_tokens(self, api_client: Client, authenticated_user):
        """Valid refresh token should return new token pair."""
        response = api_client.post(
            "/api/auth/refresh",
            data=json.dumps({
                "refresh_token": authenticated_user["refresh_token"],
            }),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "access_token" in data["data"]
        assert "refresh_token" in data["data"]

    def test_refresh_invalid_token(self, api_client: Client, db):
        """Invalid refresh token should return 401."""
        response = api_client.post(
            "/api/auth/refresh",
            data=json.dumps({"refresh_token": "invalid.token"}),
            content_type="application/json",
        )

        assert response.status_code == 401


class TestLogoutEndpoint:
    """Test POST /api/auth/logout."""

    def test_logout_returns_200(self, api_client: Client, authenticated_user):
        """Logout with valid token should return 200."""
        response = api_client.post(
            "/api/auth/logout",
            data=json.dumps({
                "refresh_token": authenticated_user["refresh_token"],
            }),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {authenticated_user['access_token']}",
        )

        assert response.status_code == 200
        assert response.json()["success"] is True

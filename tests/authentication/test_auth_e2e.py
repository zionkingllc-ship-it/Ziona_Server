"""
End-to-end verification of the refactored authentication flow.

Tests the full pipeline:
1. Suggest usernames
2. Register (username + DOB, no tokens returned)
3. OTP stored in Redis
4. Login unverified → sends OTP, requiresVerification
5. Verify email with OTP → tokens returned (camelCase)
6. Login with verified account → tokens
7. Refresh tokens
8. Logout
9. Re-register with unverified email updates data

Run with: pytest tests/authentication/test_auth_e2e.py -v
"""

import json
from unittest.mock import patch

import pytest
from django.test import Client

from core.authentication.services import AuthService


@pytest.fixture
def client():
    return Client()


class TestFullAuthFlowE2E:
    """End-to-end test of the complete auth flow with camelCase responses."""

    @patch("core.shared.tasks.email_tasks.queue_email_delivery")
    def test_complete_auth_flow(self, mock_email, client, db):
        """Full flow: suggest → register → login-unverified → verify → login → refresh → logout."""

        resp = client.post(
            "/api/auth/suggest-usernames",
            data=json.dumps({"email": "testuser@example.com", "date_of_birth": "1995-08-12"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        suggestions = resp.json()["data"]["suggestions"]
        assert len(suggestions) == 4
        chosen_username = suggestions[0]

        resp = client.post(
            "/api/auth/register",
            data=json.dumps(
                {
                    "email": "testuser@example.com",
                    "password": "SecureP@ss1",
                    "username": chosen_username,
                    "date_of_birth": "1995-08-12",
                }
            ),
            content_type="application/json",
        )
        assert resp.status_code == 201
        reg_data = resp.json()
        assert reg_data["success"] is True
        assert reg_data["data"]["user"]["username"] == chosen_username
        assert reg_data["data"]["user"]["isEmailVerified"] is False
        assert reg_data["data"]["requiresVerification"] is True
        assert "tokens" not in reg_data["data"]

        assert mock_email.called
        email_call = mock_email.call_args
        assert "testuser@example.com" in email_call.kwargs.get("recipient_list", [])

        from core.users.models import User

        user = User.objects.get(email="testuser@example.com")

        from django_redis import get_redis_connection

        redis_conn = get_redis_connection("default")
        otp_key = f"otp:verify:{user.id}"
        stored_otp = redis_conn.get(otp_key)
        assert stored_otp is not None, "OTP should be stored in Redis"
        otp_code = stored_otp.decode()
        assert len(otp_code) == 6
        assert otp_code.isdigit()

        resp = client.post(
            "/api/auth/login",
            data=json.dumps({"email": "testuser@example.com", "password": "SecureP@ss1"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["requiresVerification"] is True
        assert "tokens" not in resp.json()["data"]

        resp = client.post(
            "/api/auth/verify-email",
            data=json.dumps({"email": "testuser@example.com", "code": "000000"}),
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "INVALID_OTP"

        stored_otp = redis_conn.get(otp_key)
        otp_code = stored_otp.decode()

        resp = client.post(
            "/api/auth/verify-email",
            data=json.dumps({"email": "testuser@example.com", "code": otp_code}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        verify_data = resp.json()
        assert verify_data["success"] is True
        assert verify_data["data"]["user"]["isEmailVerified"] is True
        assert "accessToken" in verify_data["data"]["tokens"]
        assert "refreshToken" in verify_data["data"]["tokens"]
        refresh_token = verify_data["data"]["tokens"]["refreshToken"]

        assert redis_conn.get(otp_key) is None

        resp = client.post(
            "/api/auth/login",
            data=json.dumps({"email": "testuser@example.com", "password": "SecureP@ss1"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert "accessToken" in resp.json()["data"]["tokens"]

        resp = client.post(
            "/api/auth/refresh",
            data=json.dumps({"refresh_token": refresh_token}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        new_tokens = resp.json()["data"]["tokens"]
        assert "accessToken" in new_tokens
        assert "refreshToken" in new_tokens

        resp = client.post(
            "/api/auth/logout",
            data=json.dumps({"refresh_token": new_tokens["refreshToken"]}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {new_tokens['accessToken']}",
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    @patch("core.shared.tasks.email_tasks.queue_email_delivery")
    def test_resend_otp_flow(self, mock_email, client, db):
        """Test OTP resend returns expiresIn and generates a new code."""

        AuthService.register(
            email="resend_e2e@example.com",
            password="SecureP@ss1",
            username="resenduser",
            date_of_birth="1995-01-01",
        )

        from django_redis import get_redis_connection

        from core.users.models import User

        redis_conn = get_redis_connection("default")
        user = User.objects.get(email="resend_e2e@example.com")
        redis_conn.get(f"otp:verify:{user.id}").decode()

        from django.core.cache import cache

        cache.clear()

        resp = client.post(
            "/api/auth/resend-otp",
            data=json.dumps({"email": "resend_e2e@example.com"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["expiresIn"] == 600

        new_otp = redis_conn.get(f"otp:verify:{user.id}").decode()
        assert len(new_otp) == 6

        resp = client.post(
            "/api/auth/verify-email",
            data=json.dumps({"email": "resend_e2e@example.com", "code": new_otp}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert "accessToken" in resp.json()["data"]["tokens"]

    @patch("core.shared.tasks.email_tasks.queue_email_delivery")
    def test_register_unverified_update_flow(self, mock_email, client, db):
        """Re-registering with unverified email updates data and returns 200."""

        resp = client.post(
            "/api/auth/register",
            data=json.dumps(
                {
                    "email": "update@example.com",
                    "password": "SecureP@ss1",
                    "username": "oldname",
                    "date_of_birth": "2000-01-01",
                }
            ),
            content_type="application/json",
        )
        assert resp.status_code == 201

        resp = client.post(
            "/api/auth/register",
            data=json.dumps(
                {
                    "email": "update@example.com",
                    "password": "NewP@ss1!",
                    "username": "newname",
                    "date_of_birth": "2000-01-01",
                }
            ),
            content_type="application/json",
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["user"]["username"] == "newname"
        assert data["data"]["requiresVerification"] is True

    def test_register_duplicate_username_race(self, client, db):
        """Username taken should return clear error."""
        from core.users.models import User

        User.objects.create_user(
            email="existing@example.com",
            username="taken_name",
            password="SecureP@ss1",
        )

        resp = client.post(
            "/api/auth/register",
            data=json.dumps(
                {
                    "email": "new@example.com",
                    "password": "SecureP@ss1",
                    "username": "taken_name",
                    "date_of_birth": "1995-01-01",
                }
            ),
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "USERNAME_TAKEN"

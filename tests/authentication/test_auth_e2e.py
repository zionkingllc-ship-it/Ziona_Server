"""
End-to-end verification of the refactored authentication flow.

Tests the full pipeline:
1. Suggest usernames
2. Register (username + DOB, no tokens returned)
3. OTP stored in Redis
4. Verify email with OTP → tokens returned
5. Login with verified account
6. Refresh tokens
7. Logout

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
    """End-to-end test of the complete auth flow."""

    @patch("core.shared.tasks.email_tasks.send_email_async.delay")
    def test_complete_auth_flow(self, mock_email, client, db):
        """Full flow: suggest → register → verify OTP → login → refresh → logout."""

        resp = client.post(
            "/api/auth/suggest-usernames",
            data=json.dumps(
                {
                    "email": "testuser@example.com",
                    "date_of_birth": "1995-08-12",
                }
            ),
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
        assert reg_data["data"]["user"]["is_email_verified"] is False
        assert "access_token" not in reg_data["data"]
        assert "refresh_token" not in reg_data["data"]

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
            data=json.dumps(
                {
                    "email": "testuser@example.com",
                    "password": "SecureP@ss1",
                }
            ),
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "EMAIL_NOT_VERIFIED"

        resp = client.post(
            "/api/auth/verify-email",
            data=json.dumps(
                {
                    "email": "testuser@example.com",
                    "code": "000000",
                }
            ),
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "INVALID_OTP"

        resp = client.post(
            "/api/auth/verify-email",
            data=json.dumps(
                {
                    "email": "testuser@example.com",
                    "code": otp_code,
                }
            ),
            content_type="application/json",
        )
        assert resp.status_code == 200
        verify_data = resp.json()
        assert verify_data["success"] is True
        assert verify_data["data"]["user"]["is_email_verified"] is True
        assert "access_token" in verify_data["data"]
        assert "refresh_token" in verify_data["data"]
        refresh_token = verify_data["data"]["refresh_token"]

        assert redis_conn.get(otp_key) is None

        resp = client.post(
            "/api/auth/login",
            data=json.dumps(
                {
                    "email": "testuser@example.com",
                    "password": "SecureP@ss1",
                }
            ),
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert "access_token" in resp.json()["data"]

        resp = client.post(
            "/api/auth/refresh",
            data=json.dumps({"refresh_token": refresh_token}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        new_tokens = resp.json()["data"]
        assert "access_token" in new_tokens
        assert "refresh_token" in new_tokens

        resp = client.post(
            "/api/auth/logout",
            data=json.dumps({"refresh_token": new_tokens["refresh_token"]}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {new_tokens['access_token']}",
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    @patch("core.shared.tasks.email_tasks.send_email_async.delay")
    def test_resend_otp_flow(self, mock_email, client, db):
        """Test OTP resend works and generates a new code."""

        AuthService.register(
            email="resend@example.com",
            password="SecureP@ss1",
            username="resenduser",
            date_of_birth="1995-01-01",
        )

        from django_redis import get_redis_connection

        from core.users.models import User

        redis_conn = get_redis_connection("default")
        user = User.objects.get(email="resend@example.com")
        redis_conn.get(f"otp:verify:{user.id}").decode()

        resp = client.post(
            "/api/auth/resend-otp",
            data=json.dumps({"email": "resend@example.com"}),
            content_type="application/json",
        )
        assert resp.status_code == 200

        new_otp = redis_conn.get(f"otp:verify:{user.id}").decode()
        assert len(new_otp) == 6

        resp = client.post(
            "/api/auth/verify-email",
            data=json.dumps(
                {
                    "email": "resend@example.com",
                    "code": new_otp,
                }
            ),
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert "access_token" in resp.json()["data"]

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

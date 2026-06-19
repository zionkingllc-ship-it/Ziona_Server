import json
from unittest.mock import patch

from django.test import Client
from django.utils import timezone


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

    def test_register_unverified_email_overwrites_201(self, api_client: Client, create_user, db):
        """Registering with an unverified email should overwrite and return 201."""
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

        assert response.status_code == 201
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


class TestChangePasswordEndpoint:
    """Test POST /api/auth/change-password."""

    def test_change_password_success(self, api_client: Client, authenticated_user):
        """Authenticated users can change password through REST."""
        payload = {
            "currentPassword": "TestPass123!",  # pragma: allowlist secret
            "newPassword": "NewPass456!",  # pragma: allowlist secret
        }
        response = api_client.post(
            "/api/auth/change-password",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {authenticated_user['access_token']}",
        )

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["data"]["message"] == "Password changed successfully."
        assert body["data"]["signedOutDevices"] == 0

        login_response = api_client.post(
            "/api/auth/login",
            data=json.dumps(
                {
                    "email": authenticated_user["user"].email,
                    "password": "NewPass456!",  # pragma: allowlist secret
                }
            ),
            content_type="application/json",
        )
        assert login_response.status_code == 200
        assert login_response.json()["success"] is True

    def test_change_password_requires_authentication(self, api_client: Client):
        payload = {
            "currentPassword": "TestPass123!",  # pragma: allowlist secret
            "newPassword": "NewPass456!",  # pragma: allowlist secret
        }
        response = api_client.post(
            "/api/auth/change-password",
            data=json.dumps(payload),
            content_type="application/json",
        )

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHENTICATED"

    def test_change_password_rejects_wrong_current_password(
        self, api_client: Client, authenticated_user
    ):
        payload = {
            "currentPassword": "WrongPass123!",  # pragma: allowlist secret
            "newPassword": "NewPass456!",  # pragma: allowlist secret
        }
        response = api_client.post(
            "/api/auth/change-password",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {authenticated_user['access_token']}",
        )

        assert response.status_code == 400
        body = response.json()
        assert body["success"] is False
        assert body["error"]["code"] == "CURRENT_PASSWORD_INCORRECT"


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


class TestPasswordResetEndpoint:
    """Test POST /api/auth/password-reset."""

    @patch("core.shared.tasks.email_tasks.queue_email_delivery")
    def test_password_reset_request_queues_html_email(
        self, mock_email, api_client: Client, create_user
    ):
        """Password reset request queues multipart HTML email with plain fallback."""
        create_user(
            email="reset@example.com",
            username="resetuser",
            password="SecureP@ss1",
        )

        response = api_client.post(
            "/api/auth/password-reset",
            data=json.dumps({"email": "reset@example.com"}),
            content_type="application/json",
        )

        assert response.status_code == 200
        assert mock_email.called
        email_kwargs = mock_email.call_args.kwargs
        assert email_kwargs["message"]
        assert "html_message" in email_kwargs
        assert "resetuser" in email_kwargs["html_message"]
        assert "Reset Code" in email_kwargs["html_message"]


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

    def test_delete_account_requires_acknowledgement(self, api_client: Client, authenticated_user):
        """Authenticated DELETE requires explicit permanent deletion acknowledgement."""
        response = api_client.delete(
            "/api/auth/me",
            data=json.dumps({"password": "TestPass123!"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {authenticated_user['access_token']}",
        )

        assert response.status_code == 400
        body = response.json()
        assert body["success"] is False
        assert body["error"]["code"] == "DELETION_ACKNOWLEDGEMENT_REQUIRED"
        assert body["error"]["details"]["field"] == "acknowledgePermanentDeletion"
        assert body["error"]["details"]["expected"] is True

    def test_delete_account_rejects_non_boolean_acknowledgement(
        self, api_client: Client, authenticated_user
    ):
        """Deletion acknowledgement must be an explicit boolean-style value."""
        response = api_client.delete(
            "/api/auth/me",
            data=json.dumps(
                {
                    "password": "TestPass123!",
                    "acknowledgePermanentDeletion": "yes",
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {authenticated_user['access_token']}",
        )

        assert response.status_code == 400
        body = response.json()
        assert body["success"] is False
        assert body["error"]["code"] == "INVALID_DELETION_ACKNOWLEDGEMENT"
        assert body["error"]["details"]["field"] == "acknowledgePermanentDeletion"

    def test_delete_account_string_false_does_not_acknowledge(
        self, api_client: Client, authenticated_user
    ):
        """String false must not be treated as truthy by Python bool coercion."""
        from core.users.models import User

        user_id = authenticated_user["user"].id

        response = api_client.delete(
            "/api/auth/me",
            data=json.dumps(
                {
                    "password": "TestPass123!",
                    "acknowledgePermanentDeletion": "false",
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {authenticated_user['access_token']}",
        )

        assert response.status_code == 400
        body = response.json()
        assert body["success"] is False
        assert body["error"]["code"] == "DELETION_ACKNOWLEDGEMENT_REQUIRED"
        assert User.objects.filter(id=user_id).exists()

    def test_delete_account_accepts_delete_query_acknowledgement(
        self, api_client: Client, authenticated_user
    ):
        """DELETE clients without body support can pass acknowledgement in the query string."""
        response = api_client.delete(
            "/api/auth/me?acknowledgePermanentDeletion=true",
            HTTP_AUTHORIZATION=f"Bearer {authenticated_user['access_token']}",
        )

        assert response.status_code == 400
        body = response.json()
        assert body["success"] is False
        assert body["error"]["code"] == "REAUTHENTICATION_REQUIRED"

    def test_delete_account_requires_reauthentication(self, api_client: Client, authenticated_user):
        """Authenticated DELETE requires password or OTP."""
        response = api_client.delete(
            "/api/auth/me",
            data=json.dumps({"acknowledgePermanentDeletion": True}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {authenticated_user['access_token']}",
        )

        assert response.status_code == 400
        body = response.json()
        assert body["success"] is False
        assert body["error"]["code"] == "REAUTHENTICATION_REQUIRED"

    def test_delete_account_success(self, api_client: Client, authenticated_user):
        """Authenticated DELETE anonymizes and soft-deletes the user."""
        from core.users.models import User

        user_id = authenticated_user["user"].id

        assert User.objects.filter(id=user_id).exists()

        response = api_client.delete(
            "/api/auth/me",
            data=json.dumps(
                {
                    "password": "TestPass123!",
                    "acknowledgePermanentDeletion": True,
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {authenticated_user['access_token']}",
        )

        assert response.status_code == 200
        assert response.json()["success"] is True
        assert "permanently deleted" in response.json()["data"]["message"]

        assert not User.objects.filter(id=user_id).exists()
        tombstone = User.all_objects.get(id=user_id)
        assert tombstone.deleted_at is not None
        assert tombstone.is_active is False
        assert tombstone.email.startswith("deleted-")
        assert not User.all_objects.filter(email="test@example.com").exists()

    def test_delete_account_unauthorized(self, api_client: Client):
        """Unauthenticated DELETE returns 401."""
        response = api_client.delete("/api/auth/me")
        assert response.status_code == 401

    def test_delete_account_with_otp(self, api_client: Client, authenticated_user):
        """Account deletion can be protected by an account-deletion OTP."""
        from django_redis import get_redis_connection

        from core.users.models import User

        user = authenticated_user["user"]
        redis_conn = get_redis_connection("default")
        redis_conn.setex(f"otp:account_deletion:{user.id}", 600, "123456")

        response = api_client.post(
            "/api/auth/delete-account",
            data=json.dumps(
                {
                    "otp": "123456",
                    "acknowledgePermanentDeletion": True,
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {authenticated_user['access_token']}",
        )

        assert response.status_code == 200
        assert response.json()["success"] is True
        assert not User.objects.filter(id=user.id).exists()

    def test_delete_account_rejects_invalidated_sensitive_token(
        self, api_client: Client, authenticated_user
    ):
        user = authenticated_user["user"]
        user.token_invalid_before = timezone.now()
        user.save(update_fields=["token_invalid_before", "updated_at"])

        response = api_client.post(
            "/api/auth/delete-account",
            data=json.dumps(
                {
                    "password": "TestPass123!",
                    "acknowledgePermanentDeletion": True,
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {authenticated_user['access_token']}",
        )

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "INVALID_TOKEN"


class TestDeactivateAccountEndpoint:
    """Test POST /api/auth/deactivate."""

    def test_deactivate_requires_reauthentication(self, api_client: Client, authenticated_user):
        """Deactivation requires password or OTP."""
        response = api_client.post(
            "/api/auth/deactivate",
            data=json.dumps({}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {authenticated_user['access_token']}",
        )

        assert response.status_code == 400
        body = response.json()
        assert body["success"] is False
        assert body["error"]["code"] == "REAUTHENTICATION_REQUIRED"

    def test_deactivate_with_password_blocks_future_login(
        self, api_client: Client, authenticated_user
    ):
        """Deactivation keeps data but blocks subsequent authentication."""
        from core.users.models import User

        user = authenticated_user["user"]

        response = api_client.post(
            "/api/auth/deactivate",
            data=json.dumps({"password": "TestPass123!"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {authenticated_user['access_token']}",
        )

        assert response.status_code == 200
        assert response.json()["success"] is True

        user.refresh_from_db()
        assert user.is_active is False
        assert user.deleted_at is None
        assert User.objects.filter(id=user.id).exists()

        login_response = api_client.post(
            "/api/auth/login",
            data=json.dumps(
                {
                    "email": user.email,
                    "password": "TestPass123!",
                }
            ),
            content_type="application/json",
        )

        assert login_response.status_code == 403
        assert login_response.json()["error"]["code"] == "ACCOUNT_DEACTIVATED"

    def test_deactivate_with_otp(self, api_client: Client, authenticated_user):
        """Deactivation can be protected by an account-deactivation OTP."""
        from django_redis import get_redis_connection

        user = authenticated_user["user"]
        redis_conn = get_redis_connection("default")
        redis_conn.setex(f"otp:account_deactivation:{user.id}", 600, "654321")

        response = api_client.post(
            "/api/auth/deactivate",
            data=json.dumps({"otp": "654321"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {authenticated_user['access_token']}",
        )

        assert response.status_code == 200
        user.refresh_from_db()
        assert user.is_active is False

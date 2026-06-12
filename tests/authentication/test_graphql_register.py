import json
from unittest.mock import patch

import pytest
from django.test import Client

from core.authentication.services import AuthService
from core.authentication.tokens import TokenService
from core.users.models import User


@pytest.mark.django_db
@patch("core.shared.tasks.email_tasks.send_email_async.delay")
def test_graphql_register_matches_auth_service_contract(mock_email):
    client = Client()

    response = client.post(
        "/graphql/",
        data=json.dumps(
            {
                "query": """
                    mutation Register(
                      $email: String!
                      $password: String!
                      $username: String!
                      $dateOfBirth: String!
                    ) {
                      register(
                        email: $email
                        password: $password
                        username: $username
                        dateOfBirth: $dateOfBirth
                      ) {
                        success
                        message
                        requiresVerification
                        errorCode
                        user {
                          email
                          username
                          isEmailVerified
                        }
                      }
                    }
                """,
                "variables": {
                    "email": "graphql-register@example.com",
                    "password": "SecureP@ss1",
                    "username": "graphql_user",
                    "dateOfBirth": "1995-08-12",
                },
            }
        ),
        content_type="application/json",
    )

    payload = response.json()

    assert "errors" not in payload
    result = payload["data"]["register"]
    assert result["success"] is True
    assert result["message"] == "Registration successful. Check your email for verification code."
    assert result["requiresVerification"] is True
    assert result["errorCode"] is None
    assert result["user"]["email"] == "graphql-register@example.com"
    assert result["user"]["username"] == "graphql_user"
    assert result["user"]["isEmailVerified"] is False
    assert mock_email.called

    user = User.objects.get(email="graphql-register@example.com")
    assert user.username == "graphql_user"


@pytest.mark.django_db
def test_graphql_register_returns_service_validation_error_for_taken_username():
    client = Client()
    User.objects.create_user(
        email="existing@example.com",
        username="taken_name",
        password="SecureP@ss1",
    )

    response = client.post(
        "/graphql/",
        data=json.dumps(
            {
                "query": """
                    mutation Register(
                      $email: String!
                      $password: String!
                      $username: String!
                      $dateOfBirth: String!
                    ) {
                      register(
                        email: $email
                        password: $password
                        username: $username
                        dateOfBirth: $dateOfBirth
                      ) {
                        success
                        message
                        requiresVerification
                        errorCode
                        user {
                          email
                        }
                      }
                    }
                """,
                "variables": {
                    "email": "new-user@example.com",
                    "password": "SecureP@ss1",
                    "username": "taken_name",
                    "dateOfBirth": "1995-08-12",
                },
            }
        ),
        content_type="application/json",
    )

    payload = response.json()

    assert "errors" not in payload
    result = payload["data"]["register"]
    assert result["success"] is False
    assert result["message"] == "This username is already taken"
    assert result["requiresVerification"] is False
    assert result["errorCode"] == "USERNAME_TAKEN"
    assert result["user"] is None


@pytest.mark.django_db
@patch("core.shared.tasks.email_tasks.send_email_async.delay")
def test_graphql_login_returns_requires_verification_for_unverified_user(mock_email):
    user = User.objects.create_user(
        email="unverified@example.com",
        username="unverified_user",
        password="SecureP@ss1",
        is_email_verified=False,
    )
    client = Client()

    response = client.post(
        "/graphql/",
        data=json.dumps(
            {
                "query": """
                    mutation Login($email: String!, $password: String!) {
                      login(email: $email, password: $password) {
                        success
                        message
                        requiresVerification
                        accessToken
                        refreshToken
                        user {
                          email
                          username
                        }
                      }
                    }
                """,
                "variables": {
                    "email": "unverified@example.com",
                    "password": "SecureP@ss1",
                },
            }
        ),
        content_type="application/json",
    )

    payload = response.json()

    assert "errors" not in payload
    result = payload["data"]["login"]
    assert result["success"] is True
    assert result["message"] == "Email not verified. Verification code sent to your email."
    assert result["requiresVerification"] is True
    assert result["accessToken"] is None
    assert result["refreshToken"] is None
    assert result["user"]["email"] == user.email
    assert mock_email.called


@pytest.mark.django_db
@patch("core.shared.tasks.email_tasks.send_email_async.delay")
def test_graphql_verify_email_matches_auth_service_otp_flow(mock_email):
    client = Client()
    AuthService.register(
        email="verify-flow@example.com",
        password="SecureP@ss1",
        username="verifyflow",
        date_of_birth="1995-08-12",
    )

    user = User.objects.get(email="verify-flow@example.com")

    from django_redis import get_redis_connection

    redis_conn = get_redis_connection("default")
    otp_key = f"otp:verify:{user.id}"
    otp_code = redis_conn.get(otp_key).decode()

    bad_response = client.post(
        "/graphql/",
        data=json.dumps(
            {
                "query": """
                    mutation VerifyEmail($email: String!, $code: String!) {
                      verifyEmail(email: $email, code: $code) {
                        success
                        message
                        errorCode
                        accessToken
                        refreshToken
                      }
                    }
                """,
                "variables": {
                    "email": "verify-flow@example.com",
                    "code": "000000",
                },
            }
        ),
        content_type="application/json",
    )
    bad_payload = bad_response.json()
    assert "errors" not in bad_payload
    assert bad_payload["data"]["verifyEmail"]["success"] is False
    assert bad_payload["data"]["verifyEmail"]["errorCode"] == "INVALID_OTP"

    response = client.post(
        "/graphql/",
        data=json.dumps(
            {
                "query": """
                    mutation VerifyEmail($email: String!, $code: String!) {
                      verifyEmail(email: $email, code: $code) {
                        success
                        message
                        errorCode
                        accessToken
                        refreshToken
                        user {
                          email
                          isEmailVerified
                        }
                      }
                    }
                """,
                "variables": {
                    "email": "verify-flow@example.com",
                    "code": otp_code,
                },
            }
        ),
        content_type="application/json",
    )

    payload = response.json()

    assert "errors" not in payload
    result = payload["data"]["verifyEmail"]
    assert result["success"] is True
    assert result["message"] == "Email verified successfully"
    assert result["errorCode"] is None
    assert result["accessToken"]
    assert result["refreshToken"]
    assert result["user"]["email"] == "verify-flow@example.com"
    assert result["user"]["isEmailVerified"] is True
    assert redis_conn.get(otp_key) is None
    assert mock_email.called


@pytest.mark.django_db
@patch("core.shared.tasks.email_tasks.send_email_async.delay")
def test_graphql_resend_verification_otp_matches_auth_service_flow(mock_email):
    client = Client()
    AuthService.register(
        email="resend-flow@example.com",
        password="SecureP@ss1",
        username="resendflow",
        date_of_birth="1995-08-12",
    )

    response = client.post(
        "/graphql/",
        data=json.dumps(
            {
                "query": """
                    mutation ResendVerificationOtp($email: String!) {
                      resendVerificationOtp(email: $email) {
                        success
                        message
                        expiresIn
                        resendAfter
                        purpose
                        errorCode
                      }
                    }
                """,
                "variables": {
                    "email": "resend-flow@example.com",
                },
            }
        ),
        content_type="application/json",
    )

    payload = response.json()

    assert "errors" not in payload
    result = payload["data"]["resendVerificationOtp"]
    assert result["success"] is True
    assert result["message"] == "Verification code sent to your email."
    assert result["expiresIn"] == 600
    assert result["purpose"] == "email_verification"
    assert result["errorCode"] is None
    assert mock_email.called


@pytest.mark.django_db
def test_graphql_suggest_usernames_accepts_date_of_birth_argument():
    client = Client()

    response = client.post(
        "/graphql/",
        data=json.dumps(
            {
                "query": """
                    query SuggestUsernames($email: String!, $dateOfBirth: String) {
                      suggestUsernames(email: $email, dateOfBirth: $dateOfBirth)
                    }
                """,
                "variables": {
                    "email": "jane.doe@example.com",
                    "dateOfBirth": "1995-08-12",
                },
            }
        ),
        content_type="application/json",
    )

    payload = response.json()

    assert "errors" not in payload
    suggestions = payload["data"]["suggestUsernames"]
    assert len(suggestions) == 4


@pytest.mark.django_db
@patch("core.shared.tasks.email_tasks.send_email_async.delay")
def test_graphql_reset_password_matches_public_request_flow(mock_email):
    User.objects.create_user(
        email="reset-request@example.com",
        username="resetrequest",
        password="SecureP@ss1",
    )
    client = Client()

    response = client.post(
        "/graphql/",
        data=json.dumps(
            {
                "query": """
                    mutation ResetPassword($email: String!) {
                      resetPassword(email: $email) {
                        success
                        message
                        errorCode
                      }
                    }
                """,
                "variables": {
                    "email": "reset-request@example.com",
                },
            }
        ),
        content_type="application/json",
    )

    payload = response.json()

    assert "errors" not in payload
    result = payload["data"]["resetPassword"]
    assert result["success"] is True
    assert result["message"] == "If an account with this email exists, a reset code has been sent."
    assert result["errorCode"] is None
    assert mock_email.called


@pytest.mark.django_db
@patch("core.shared.tasks.email_tasks.send_email_async.delay")
def test_graphql_confirm_password_reset_matches_auth_service_flow(mock_email):
    user = User.objects.create_user(
        email="reset-confirm@example.com",
        username="resetconfirm",
        password="OldSecureP@ss1",
    )
    client = Client()

    from django_redis import get_redis_connection

    from core.authentication.otp_service import OTPService

    OTPService.unified_send_otp(email=user.email, purpose="password_reset")
    redis_conn = get_redis_connection("default")
    otp_code = redis_conn.get(f"otp:password_reset:{user.id}").decode()

    verify_response = client.post(
        "/graphql/",
        data=json.dumps(
            {
                "query": """
                    mutation VerifyOtp($email: String!, $code: String!, $purpose: String!) {
                      verifyOtp(email: $email, code: $code, purpose: $purpose) {
                        success
                        resetToken
                        errorCode
                      }
                    }
                """,
                "variables": {
                    "email": user.email,
                    "code": otp_code,
                    "purpose": "password_reset",
                },
            }
        ),
        content_type="application/json",
    )
    verify_payload = verify_response.json()
    assert "errors" not in verify_payload
    reset_token = verify_payload["data"]["verifyOtp"]["resetToken"]
    assert reset_token

    response = client.post(
        "/graphql/",
        data=json.dumps(
            {
                "query": """
                    mutation ConfirmPasswordReset($resetToken: String!, $newPassword: String!) {
                      confirmPasswordReset(resetToken: $resetToken, newPassword: $newPassword) {
                        success
                        message
                        errorCode
                        accessToken
                        refreshToken
                        user {
                          email
                        }
                      }
                    }
                """,
                "variables": {
                    "resetToken": reset_token,
                    "newPassword": "BrandNewP@ss1",
                },
            }
        ),
        content_type="application/json",
    )

    payload = response.json()

    assert "errors" not in payload
    result = payload["data"]["confirmPasswordReset"]
    assert result["success"] is True
    assert result["message"] == "Password reset successfully"
    assert result["errorCode"] is None
    assert result["accessToken"]
    assert result["refreshToken"]
    assert result["user"]["email"] == user.email
    assert mock_email.called


@pytest.mark.django_db
def test_graphql_finalize_username_uses_auth_service_and_exposes_flag():
    user = User.objects.create_user(
        email="graphql-finalize@example.com",
        username="temp_graphql_user",
        password="SecureP@ss1",
        needs_username_selection=True,
    )
    access_token = TokenService.generate_access_token(str(user.id), user.role)
    client = Client()

    response = client.post(
        "/graphql/",
        data=json.dumps(
            {
                "query": """
                    mutation FinalizeUsername($username: String!) {
                      finalizeUsername(username: $username) {
                        success
                        message
                        errorCode
                        user {
                          username
                          needsUsernameSelection
                        }
                      }
                    }
                """,
                "variables": {
                    "username": "graphql_finalized",
                },
            }
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {access_token}",
    )

    payload = response.json()

    assert "errors" not in payload
    result = payload["data"]["finalizeUsername"]
    assert result["success"] is True
    assert result["errorCode"] is None
    assert result["user"]["username"] == "graphql_finalized"
    assert result["user"]["needsUsernameSelection"] is False


@pytest.mark.django_db
def test_graphql_add_password_uses_auth_service_flow():
    user = User.objects.create(
        email="graphql-oauth@example.com",
        username="graphqloauth",
        auth_provider="google",
        is_email_verified=True,
    )
    user.set_unusable_password()
    user.save()

    access_token = TokenService.generate_access_token(str(user.id), user.role)
    client = Client()

    response = client.post(
        "/graphql/",
        data=json.dumps(
            {
                "query": """
                    mutation AddPassword($password: String!) {
                      addPassword(password: $password) {
                        success
                        message
                        errorCode
                        user {
                          email
                        }
                      }
                    }
                """,
                "variables": {
                    "password": "FreshSecureP@ss1",
                },
            }
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {access_token}",
    )

    payload = response.json()

    assert "errors" not in payload
    result = payload["data"]["addPassword"]
    assert result["success"] is True
    assert result["errorCode"] is None
    assert result["user"]["email"] == user.email

    user.refresh_from_db()
    assert user.check_password("FreshSecureP@ss1") is True


@pytest.mark.django_db
def test_graphql_change_password_uses_auth_service_flow():
    user = User.objects.create_user(
        email="graphql-change@example.com",
        username="graphqlchange",
        password="OldSecureP@ss1",
        is_email_verified=True,
    )
    access_token = TokenService.generate_access_token(str(user.id), user.role)
    client = Client()

    response = client.post(
        "/graphql/",
        data=json.dumps(
            {
                "query": """
                    mutation ChangePassword($currentPassword: String!, $newPassword: String!) {
                      changePassword(
                        currentPassword: $currentPassword
                        newPassword: $newPassword
                      ) {
                        success
                        message
                        signedOutDevices
                        errorCode
                      }
                    }
                """,
                "variables": {
                    "currentPassword": "OldSecureP@ss1",
                    "newPassword": "NewSecureP@ss2",
                },
            }
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {access_token}",
    )

    payload = response.json()

    assert "errors" not in payload
    result = payload["data"]["changePassword"]
    assert result["success"] is True
    assert result["message"] == "Password changed successfully."
    assert result["signedOutDevices"] == 0
    assert result["errorCode"] is None

    user.refresh_from_db()
    assert user.check_password("NewSecureP@ss2") is True

"""Account pre-hijacking: an unverified signup's password must not survive OAuth.

Attack: someone registers the victim's email with a password and never
verifies it (they can't — the OTP goes to the victim). Later the victim signs
in with Google or Apple, which verifies the email and links the account. If the
signup password survived that, the attacker could now log in to the victim's
real account.
"""

import json
from unittest.mock import patch

import pytest
from django.conf import settings
from django.urls import reverse

from core.authentication.password_service import PasswordService
from core.authentication.services import AuthService
from core.authentication.validators import AuthenticationError
from core.users.models import User
from tests.authentication.test_apple_oauth import (  # noqa: F401 — fixtures used by name
    _apple_token,
    _cache_nonce,
    apple_private_key,
    configure_apple_oauth,
)

VICTIM_EMAIL = "victim@example.com"
ATTACKER_PASSWORD = "Att4cker!Pass"  # pragma: allowlist secret
OWNER_PASSWORD = "Own3r!NewPass"  # pragma: allowlist secret


@pytest.fixture(autouse=True)
def _google_settings(settings):
    settings.GOOGLE_CLIENT_ID = "test-web-client.apps.googleusercontent.com"
    settings.GOOGLE_CLIENT_IDS = [settings.GOOGLE_CLIENT_ID]


@pytest.fixture
def attacker_signup():
    """The attacker's half: a real registration they can never verify."""
    with patch("core.shared.tasks.email_tasks.queue_email_delivery"):
        AuthService.register(
            email=VICTIM_EMAIL,
            password=ATTACKER_PASSWORD,
            username="not_the_victim",
            date_of_birth="1990-01-01",
        )
    user = User.objects.get(email=VICTIM_EMAIL)
    assert user.is_email_verified is False and user.has_usable_password()
    return user


def _victim_google_sign_in(api_client):
    with patch("google.oauth2.id_token.verify_oauth2_token") as verify:
        verify.return_value = {
            "aud": settings.GOOGLE_CLIENT_ID,
            "email": VICTIM_EMAIL,
            "sub": "victim_google_id",
            "email_verified": True,
        }
        return api_client.post(
            reverse("authentication:google-oauth"),
            data=json.dumps({"id_token": "victim_token"}),
            content_type="application/json",
        )


def _assert_attacker_locked_out():
    with pytest.raises(AuthenticationError) as exc:
        AuthService.login(VICTIM_EMAIL, ATTACKER_PASSWORD)
    assert exc.value.code == "INVALID_CREDENTIALS"


@pytest.mark.django_db
def test_attacker_password_dies_when_victim_signs_in_with_google(api_client, attacker_signup):
    assert _victim_google_sign_in(api_client).status_code == 200

    user = User.objects.get(pk=attacker_signup.pk)
    assert user.is_email_verified is True
    assert user.google_id == "victim_google_id"
    assert user.has_usable_password() is False
    _assert_attacker_locked_out()


@pytest.mark.django_db
def test_attacker_password_dies_when_victim_signs_in_with_apple(
    api_client, attacker_signup, request
):
    apple_key = request.getfixturevalue("apple_private_key")
    _cache_nonce("victim-nonce")
    token = _apple_token(
        apple_key, sub="victim-apple-sub", raw_nonce="victim-nonce", email=VICTIM_EMAIL
    )

    response = api_client.post(
        reverse("authentication:apple-oauth"),
        data=json.dumps({"identityToken": token, "rawNonce": "victim-nonce"}),
        content_type="application/json",
    )

    assert response.status_code == 200
    assert User.objects.get(pk=attacker_signup.pk).has_usable_password() is False
    _assert_attacker_locked_out()


@pytest.mark.django_db
def test_discard_is_recorded_as_a_security_event(api_client, attacker_signup):
    with patch("core.authentication.oauth_service.log_security_event") as log_event:
        _victim_google_sign_in(api_client)

    events = [c.args[0] for c in log_event.call_args_list]
    assert "auth.oauth.unverified_password_discarded" in events


@pytest.mark.django_db
def test_owner_can_set_a_password_afterwards_with_forgot_password(api_client, attacker_signup):
    from django_redis import get_redis_connection  # patched per test; import late

    _victim_google_sign_in(api_client)

    with patch("core.shared.tasks.email_tasks.queue_email_delivery"):
        PasswordService.request_password_reset(VICTIM_EMAIL)
    otp = get_redis_connection("default").get(f"otp:password_reset:{attacker_signup.pk}")
    PasswordService.reset_password(VICTIM_EMAIL, otp.decode(), OWNER_PASSWORD)

    assert AuthService.login(VICTIM_EMAIL, OWNER_PASSWORD)["access_token"]
    _assert_attacker_locked_out()


@pytest.mark.django_db
def test_verified_password_account_keeps_its_password(api_client):
    """Only an unproven password goes. A verified owner's password is theirs."""
    User.objects.create_user(
        email=VICTIM_EMAIL,
        username="real_owner",
        password=OWNER_PASSWORD,
        is_email_verified=True,
    )

    assert _victim_google_sign_in(api_client).status_code == 200

    assert AuthService.login(VICTIM_EMAIL, OWNER_PASSWORD)["access_token"]

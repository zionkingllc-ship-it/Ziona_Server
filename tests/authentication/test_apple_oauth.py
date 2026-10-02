import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import jwt
import pytest
import requests
from cryptography.hazmat.primitives.asymmetric import rsa
from django.core.cache import cache
from django.urls import reverse
from jwt.algorithms import RSAAlgorithm

from core.authentication.apple_oauth import _sha256
from core.users.models import User

APPLE_AUDIENCE = "com.ziona.ios"
APPLE_ISSUER = "https://appleid.apple.com"
APPLE_KID = "apple-test-kid"


@pytest.fixture(autouse=True)
def configure_apple_oauth(settings):
    settings.APPLE_CLIENT_ID = APPLE_AUDIENCE
    settings.APPLE_CLIENT_IDS = [APPLE_AUDIENCE]
    settings.APPLE_ID_TOKEN_ISSUER = APPLE_ISSUER
    settings.APPLE_PUBLIC_KEYS_CACHE_KEY = "test_apple_public_keys"
    settings.APPLE_PUBLIC_KEYS_CACHE_TIMEOUT = 86400
    settings.APPLE_PUBLIC_KEYS_REQUEST_TIMEOUT = 1
    settings.APPLE_NONCE_TTL_SECONDS = 600
    settings.APPLE_REQUIRE_SERVER_NONCE = True
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def apple_private_key(settings):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    public_jwk.update({"kid": APPLE_KID, "alg": "RS256", "use": "sig"})
    cache.set(settings.APPLE_PUBLIC_KEYS_CACHE_KEY, [public_jwk], timeout=86400)
    return private_key


def _cache_nonce(raw_nonce: str) -> str:
    nonce = _sha256(raw_nonce)
    cache.set(f"apple_signin_nonce:{nonce}", True, timeout=600)
    return nonce


def _apple_token(
    private_key,
    *,
    sub: str = "apple-sub-123",
    aud: str = APPLE_AUDIENCE,
    raw_nonce: str = "raw-nonce-123",
    email: str | None = "apple.user@example.com",
    email_verified: str | bool = "true",
    expires_delta: timedelta = timedelta(minutes=5),
):
    now = datetime.now(timezone.utc)
    payload = {
        "iss": APPLE_ISSUER,
        "aud": aud,
        "sub": sub,
        "iat": now,
        "exp": now + expires_delta,
        "nonce": _sha256(raw_nonce),
    }
    if email is not None:
        payload["email"] = email
        payload["email_verified"] = email_verified

    return jwt.encode(
        payload,
        private_key,
        algorithm="RS256",
        headers={"kid": APPLE_KID, "alg": "RS256"},
    )


@pytest.mark.django_db
class TestAppleOAuth:
    url = reverse("authentication:apple-oauth")
    nonce_url = reverse("authentication:apple-nonce")

    def test_apple_nonce_endpoint_issues_server_nonce(self, api_client):
        response = api_client.post(self.nonce_url, data="{}", content_type="application/json")

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["rawNonce"]
        assert data["nonce"] == _sha256(data["rawNonce"])
        assert data["expiresIn"] == 600
        assert cache.get(f"apple_signin_nonce:{data['nonce']}") is True

    def test_new_apple_user_first_login(self, api_client, apple_private_key):
        raw_nonce = "first-login-nonce"
        _cache_nonce(raw_nonce)
        token = _apple_token(
            apple_private_key,
            sub="apple-first-sub",
            raw_nonce=raw_nonce,
            email="new.apple@example.com",
        )

        response = api_client.post(
            self.url,
            data=json.dumps(
                {
                    "identityToken": token,
                    "rawNonce": raw_nonce,
                    "user": {
                        "name": {"firstName": "Apple", "lastName": "User"},
                        "email": "new.apple@example.com",
                    },
                }
            ),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["isNewUser"] is True
        assert data["data"]["user"]["email"] == "new.apple@example.com"
        assert data["data"]["user"]["needsUsernameSelection"] is True
        assert "accessToken" in data["data"]["tokens"]

        user = User.objects.get(email="new.apple@example.com")
        assert user.social_auth_provider == "apple"
        assert user.auth_provider == "apple"
        assert user.apple_sub == "apple-first-sub"
        assert user.full_name == "Apple User"
        assert user.is_email_verified is True
        assert not user.has_usable_password()

    def test_existing_apple_user_can_login_without_email(self, api_client, apple_private_key):
        raw_nonce = "subsequent-login-nonce"
        _cache_nonce(raw_nonce)
        user = User.objects.create_user(
            email="existing.apple@example.com",
            username="existing_apple",
            social_auth_provider="apple",
            auth_provider="apple",
            apple_sub="apple-existing-sub",
            is_email_verified=True,
        )
        user.set_unusable_password()
        user.save()

        token = _apple_token(
            apple_private_key,
            sub="apple-existing-sub",
            raw_nonce=raw_nonce,
            email=None,
        )

        response = api_client.post(
            self.url,
            data=json.dumps({"identityToken": token, "rawNonce": raw_nonce}),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["isNewUser"] is False
        assert data["data"]["user"]["email"] == "existing.apple@example.com"

    def test_first_apple_login_without_email_is_rejected(self, api_client, apple_private_key):
        raw_nonce = "missing-email-nonce"
        _cache_nonce(raw_nonce)
        token = _apple_token(
            apple_private_key,
            sub="apple-no-email-sub",
            raw_nonce=raw_nonce,
            email=None,
        )

        response = api_client.post(
            self.url,
            data=json.dumps({"identityToken": token, "rawNonce": raw_nonce}),
            content_type="application/json",
        )

        assert response.status_code == 400
        data = response.json()
        assert data["success"] is False
        assert data["error"]["code"] == "APPLE_EMAIL_REQUIRED"

    def test_password_account_apple_auto_links_when_email_verified(
        self, api_client, apple_private_key
    ):
        """Verified Apple email on an existing password account auto-links (dual login)."""
        User.objects.create_user(
            email="password.apple@example.com",
            username="passwordapple",
            password="SecurePass1!",  # pragma: allowlist secret
            is_email_verified=True,
        )
        raw_nonce = "password-link-nonce"
        _cache_nonce(raw_nonce)
        token = _apple_token(
            apple_private_key,
            sub="apple-linked-sub",
            raw_nonce=raw_nonce,
            email="password.apple@example.com",
            email_verified="true",
        )

        response = api_client.post(
            self.url,
            data=json.dumps({"identityToken": token, "rawNonce": raw_nonce}),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["isNewUser"] is False

        user = User.objects.get(email="password.apple@example.com")
        assert user.apple_sub == "apple-linked-sub"
        assert user.has_usable_password() is True  # password kept — dual login

    def test_password_account_apple_blocked_when_email_unverified(
        self, api_client, apple_private_key
    ):
        """An UNVERIFIED Apple email must not take over an existing password account."""
        User.objects.create_user(
            email="secure.apple@example.com",
            username="secureapple",
            password="SecurePass1!",  # pragma: allowlist secret
            is_email_verified=True,
        )
        raw_nonce = "password-block-nonce"
        _cache_nonce(raw_nonce)
        token = _apple_token(
            apple_private_key,
            sub="apple-attacker-sub",
            raw_nonce=raw_nonce,
            email="secure.apple@example.com",
            email_verified="false",
        )

        response = api_client.post(
            self.url,
            data=json.dumps({"identityToken": token, "rawNonce": raw_nonce}),
            content_type="application/json",
        )

        assert response.status_code == 409
        data = response.json()
        assert data["success"] is False
        assert data["error"]["code"] == "EMAIL_REGISTERED_WITH_PASSWORD"
        assert User.objects.get(email="secure.apple@example.com").apple_sub is None

    def test_unverified_password_signup_can_continue_with_apple_oauth(
        self, api_client, apple_private_key
    ):
        User.objects.create_user(
            email="pending.apple@example.com",
            username="pendingapple",
            password="SecurePass1!",  # pragma: allowlist secret
            is_email_verified=False,
        )
        raw_nonce = "pending-apple-nonce"
        _cache_nonce(raw_nonce)
        token = _apple_token(
            apple_private_key,
            sub="apple-pending-sub",
            raw_nonce=raw_nonce,
            email="pending.apple@example.com",
        )

        response = api_client.post(
            self.url,
            data=json.dumps(
                {
                    "identityToken": token,
                    "rawNonce": raw_nonce,
                    "user": {
                        "name": {"firstName": "Pending", "lastName": "Apple"},
                        "email": "pending.apple@example.com",
                    },
                }
            ),
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["isNewUser"] is False

        user = User.objects.get(email="pending.apple@example.com")
        assert user.apple_sub == "apple-pending-sub"
        assert user.is_email_verified is True
        # The signup password was never proven to own this inbox, so it is
        # dropped; Apple is now the only way in until Forgot Password.
        assert user.has_usable_password() is False
        assert user.social_auth_provider == "apple"
        assert user.full_name == "Pending Apple"

    def test_accepts_apple_token_from_additional_configured_bundle(
        self, api_client, apple_private_key, settings
    ):
        """A second bundle in APPLE_CLIENT_IDS is accepted.

        This is how the staging build (com.zionking.ziona.staging) signs in
        alongside production without a code change — the allowlist is a list and
        is passed straight to jwt.decode(audience=[...]).
        """
        staging_bundle = "com.zionking.ziona.staging"
        settings.APPLE_CLIENT_IDS = [APPLE_AUDIENCE, staging_bundle]
        raw_nonce = "staging-bundle-nonce"
        _cache_nonce(raw_nonce)
        token = _apple_token(
            apple_private_key,
            sub="apple-sub-staging",
            raw_nonce=raw_nonce,
            aud=staging_bundle,
            email="staging.user@example.com",
        )

        response = api_client.post(
            self.url,
            data=json.dumps({"identityToken": token, "rawNonce": raw_nonce}),
            content_type="application/json",
        )

        assert response.status_code == 200, response.json()
        assert response.json()["success"] is True
        assert User.objects.filter(email="staging.user@example.com").exists()

    def test_rejects_unconfigured_apple_audience(self, api_client, apple_private_key):
        raw_nonce = "bad-audience-nonce"
        _cache_nonce(raw_nonce)
        token = _apple_token(
            apple_private_key,
            raw_nonce=raw_nonce,
            aud="com.attacker.app",
        )

        response = api_client.post(
            self.url,
            data=json.dumps({"identityToken": token, "rawNonce": raw_nonce}),
            content_type="application/json",
        )

        assert response.status_code == 400
        data = response.json()
        assert data["success"] is False
        assert data["error"]["code"] == "INVALID_OAUTH_TOKEN"
        assert data["error"]["message"] == "Invalid Apple token audience"

    def test_rejects_nonce_mismatch(self, api_client, apple_private_key):
        _cache_nonce("expected-nonce")
        token = _apple_token(
            apple_private_key,
            raw_nonce="actual-token-nonce",
        )

        response = api_client.post(
            self.url,
            data=json.dumps({"identityToken": token, "rawNonce": "expected-nonce"}),
            content_type="application/json",
        )

        assert response.status_code == 400
        data = response.json()
        assert data["success"] is False
        assert data["error"]["code"] == "APPLE_NONCE_MISMATCH"

    def test_rejects_expired_apple_token(self, api_client, apple_private_key):
        raw_nonce = "expired-nonce"
        _cache_nonce(raw_nonce)
        token = _apple_token(
            apple_private_key,
            raw_nonce=raw_nonce,
            expires_delta=timedelta(minutes=-1),
        )

        response = api_client.post(
            self.url,
            data=json.dumps({"identityToken": token, "rawNonce": raw_nonce}),
            content_type="application/json",
        )

        assert response.status_code == 401
        data = response.json()
        assert data["success"] is False
        assert data["error"]["code"] == "APPLE_TOKEN_EXPIRED"

    def test_rejects_invalid_signature(self, api_client, apple_private_key):
        raw_nonce = "invalid-signature-nonce"
        _cache_nonce(raw_nonce)
        wrong_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        token = _apple_token(
            wrong_private_key,
            raw_nonce=raw_nonce,
        )

        response = api_client.post(
            self.url,
            data=json.dumps({"identityToken": token, "rawNonce": raw_nonce}),
            content_type="application/json",
        )

        assert response.status_code == 400
        data = response.json()
        assert data["success"] is False
        assert data["error"]["code"] == "INVALID_OAUTH_TOKEN"
        assert data["error"]["message"] == "Invalid Apple authentication token signature"

    def test_apple_key_fetch_timeout_returns_service_unavailable(self, api_client):
        raw_nonce = "timeout-nonce"
        _cache_nonce(raw_nonce)
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        token = _apple_token(private_key, raw_nonce=raw_nonce)

        with patch(
            "core.authentication.apple_oauth.requests.get",
            side_effect=requests.Timeout("timed out"),
        ):
            response = api_client.post(
                self.url,
                data=json.dumps({"identityToken": token, "rawNonce": raw_nonce}),
                content_type="application/json",
            )

        assert response.status_code == 503
        data = response.json()
        assert data["success"] is False
        assert data["error"]["code"] == "APPLE_KEYS_TIMEOUT"


def _google_registered_user(email: str, *, google_id: str = "google_id_1") -> User:
    """An account created by Google sign-in: no usable password."""
    user = User.objects.create_user(
        email=email,
        username=f"google_{google_id}",
        auth_provider="google",
        social_auth_provider="google",
        google_id=google_id,
        is_email_verified=True,
    )
    user.set_unusable_password()
    user.save(update_fields=["password"])
    return user


@pytest.mark.django_db
class TestAppleLinksGoogleAccount:
    """A user who registered with Google can later sign in with Apple."""

    url = reverse("authentication:apple-oauth")

    def _sign_in(self, api_client, private_key, *, email, sub, verified="true", nonce):
        _cache_nonce(nonce)
        token = _apple_token(
            private_key, sub=sub, raw_nonce=nonce, email=email, email_verified=verified
        )
        return api_client.post(
            self.url,
            data=json.dumps({"identityToken": token, "rawNonce": nonce}),
            content_type="application/json",
        )

    def test_verified_apple_email_links_to_google_account(self, api_client, apple_private_key):
        google_user = _google_registered_user("both@example.com")

        response = self._sign_in(
            api_client,
            apple_private_key,
            email="both@example.com",
            sub="apple-both",
            nonce="link-nonce",
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["data"]["isNewUser"] is False
        assert User.objects.filter(email="both@example.com").count() == 1

        user = User.objects.get(pk=google_user.pk)
        assert user.apple_sub == "apple-both"
        assert user.google_id == "google_id_1"
        assert user.social_auth_provider == "google"
        assert user.auth_provider == "google"

    def test_provider_does_not_flip_on_later_apple_sign_ins(self, api_client, apple_private_key):
        google_user = _google_registered_user("repeat@example.com")

        for i in range(2):
            response = self._sign_in(
                api_client,
                apple_private_key,
                email="repeat@example.com",
                sub="apple-repeat",
                nonce=f"repeat-nonce-{i}",
            )
            assert response.status_code == 200

        user = User.objects.get(pk=google_user.pk)
        assert user.social_auth_provider == "google"
        assert user.auth_provider == "google"

    def test_unverified_apple_email_cannot_join_google_account(self, api_client, apple_private_key):
        google_user = _google_registered_user("victim@example.com")

        response = self._sign_in(
            api_client,
            apple_private_key,
            email="victim@example.com",
            sub="apple-attacker",
            verified="false",
            nonce="unverified-nonce",
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "EMAIL_REGISTERED_WITH_DIFFERENT_PROVIDER"
        assert User.objects.get(pk=google_user.pk).apple_sub is None

    def test_apple_account_mismatch_still_enforced_on_linked_google_account(
        self, api_client, apple_private_key
    ):
        google_user = _google_registered_user("linked@example.com")
        User.objects.filter(pk=google_user.pk).update(apple_sub="apple-original")

        response = self._sign_in(
            api_client,
            apple_private_key,
            email="linked@example.com",
            sub="apple-other",
            nonce="mismatch-nonce",
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "APPLE_ACCOUNT_MISMATCH"
        assert User.objects.get(pk=google_user.pk).apple_sub == "apple-original"

    def test_other_social_providers_are_still_refused(self, api_client, apple_private_key):
        User.objects.create_user(
            email="fb@example.com", username="fb_user", social_auth_provider="facebook"
        )

        response = self._sign_in(
            api_client,
            apple_private_key,
            email="fb@example.com",
            sub="apple-fb",
            nonce="fb-nonce",
        )

        assert response.status_code == 409
        error = response.json()["error"]
        assert error["code"] == "EMAIL_REGISTERED_WITH_DIFFERENT_PROVIDER"
        assert "facebook instead" in error["message"]

from datetime import timedelta

import jwt
import pytest
from django.conf import settings
from django.utils import timezone

from core.authentication.tokens import TokenError, TokenInfrastructureError, TokenService


class TestAccessToken:
    """Test access token generation and validation."""

    def test_generate_access_token(self, create_user):
        """Access token should be a valid JWT string."""
        user = create_user()
        token = TokenService.generate_access_token(str(user.id), user.role)

        assert isinstance(token, str)
        assert len(token) > 0

    def test_access_token_lifetime_is_one_day(self, create_user):
        """Access tokens should last 24 hours."""
        user = create_user()
        token = TokenService.generate_access_token(str(user.id), user.role)
        payload = TokenService.validate_access_token(token)

        assert payload["exp"] - payload["iat"] == int(
            settings.JWT_ACCESS_TOKEN_LIFETIME.total_seconds()
        )

    def test_validate_access_token(self, create_user):
        """Valid access token should decode with correct payload."""
        user = create_user()
        token = TokenService.generate_access_token(str(user.id), user.role)
        payload = TokenService.validate_access_token(token)

        assert payload["user_id"] == str(user.id)
        assert payload["role"] == "user"
        assert payload["type"] == "access"

    def test_validate_access_token_allows_small_iat_clock_skew(self, create_user):
        """Small clock skew should not reject a freshly issued mobile token."""
        user = create_user()
        now = timezone.now()
        payload = {
            "user_id": str(user.id),
            "role": user.role,
            "type": "access",
            "iat": now + timedelta(seconds=settings.JWT_LEEWAY_SECONDS - 1),
            "exp": now + settings.JWT_ACCESS_TOKEN_LIFETIME,
            "jti": "clock-skew-test",
        }
        token = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)

        decoded = TokenService.validate_access_token(token)

        assert decoded["user_id"] == str(user.id)

    def test_invalid_access_token(self):
        """Invalid token should raise TokenError."""
        with pytest.raises(TokenError, match="Invalid access token"):
            TokenService.validate_access_token("invalid.token.here")

    def test_wrong_token_type(self, create_user):
        """Refresh token should not pass as access token."""
        user = create_user()
        refresh_token, _ = TokenService.generate_refresh_token(str(user.id))

        with pytest.raises(TokenError, match="not an access token"):
            TokenService.validate_access_token(refresh_token)


class TestRefreshToken:
    """Test refresh token generation and validation."""

    def test_generate_refresh_token(self, create_user):
        """Refresh token should return token and jti."""
        user = create_user()
        token, jti = TokenService.generate_refresh_token(str(user.id))

        assert isinstance(token, str)
        assert isinstance(jti, str)
        assert len(token) > 0
        assert len(jti) > 0

    def test_rotate_refresh_token(self, create_user):
        """Token rotation should return new access and refresh tokens."""
        user = create_user()
        old_token, _ = TokenService.generate_refresh_token(str(user.id))

        result = TokenService.rotate_refresh_token(old_token, user.role)

        assert "access_token" in result
        assert "refresh_token" in result
        assert result["access_token"] != old_token
        assert result["refresh_token"] != old_token

    def test_rotate_refresh_token_replay_uses_grace_result(self, create_user):
        """Duplicate mobile refresh requests should receive the same rotated pair."""
        user = create_user()
        old_token, _ = TokenService.generate_refresh_token(str(user.id))

        first = TokenService.rotate_refresh_token(old_token, user.role)
        second = TokenService.rotate_refresh_token(old_token, user.role)

        assert second == first

    def test_generate_refresh_token_fails_closed_when_redis_is_required(
        self, create_user, settings, monkeypatch
    ):
        user = create_user()
        settings.AUTH_STRICT_REDIS = True

        def _boom(_alias):
            raise RuntimeError("redis down")

        monkeypatch.setattr("django_redis.get_redis_connection", _boom)

        with pytest.raises(TokenInfrastructureError, match="Unable to create a session"):
            TokenService.generate_refresh_token(str(user.id))

    def test_validate_refresh_token_fails_closed_when_redis_is_required(
        self, create_user, settings, monkeypatch
    ):
        user = create_user()
        settings.AUTH_STRICT_REDIS = False
        refresh_token, _ = TokenService.generate_refresh_token(str(user.id))
        settings.AUTH_STRICT_REDIS = True

        def _boom(_alias):
            raise RuntimeError("redis down")

        monkeypatch.setattr("django_redis.get_redis_connection", _boom)

        with pytest.raises(TokenInfrastructureError, match="Unable to validate your session"):
            TokenService.validate_refresh_token(refresh_token)


class TestSensitiveAccessTokenValidation:
    def test_sensitive_validation_rejects_invalidated_token(self, create_user):
        user = create_user()
        token = TokenService.generate_access_token(str(user.id), user.role)

        user.token_invalid_before = timezone.now()
        user.save(update_fields=["token_invalid_before", "updated_at"])

        with pytest.raises(TokenError, match="invalidated"):
            TokenService.validate_access_token(token, enforce_revocation=True)


class TestTokenBlacklist:
    """Test token blacklisting (logout)."""

    def test_blacklist_writes_to_redis(self, create_user):
        """
        blacklist_access_token should write the JTI to Redis.

        Design note: validate_access_token is intentionally CPU-only and does
        NOT check the Redis blacklist on every request (see tokens.py docstring).
        The access token TTL keeps the blacklist window bounded. The refresh
        token is revoked immediately, so no new access tokens can be issued
        post-logout.
        """
        from django_redis import get_redis_connection

        user = create_user()
        token = TokenService.generate_access_token(str(user.id), user.role)
        payload = TokenService.validate_access_token(token)
        jti = payload["jti"]

        TokenService.blacklist_access_token(token)

        redis_conn = get_redis_connection("default")
        assert redis_conn.exists(
            f"blacklist:{jti}"
        ), "blacklist_access_token should store the JTI in Redis"

    def test_valid_token_still_validates(self, create_user):
        """A non-blacklisted access token should validate without error."""
        user = create_user()
        token = TokenService.generate_access_token(str(user.id), user.role)
        payload = TokenService.validate_access_token(token)

        assert payload["user_id"] == str(user.id)
        assert payload["type"] == "access"

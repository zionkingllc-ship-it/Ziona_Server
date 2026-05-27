import pytest
from django.conf import settings

from core.authentication.tokens import TokenError, TokenService


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

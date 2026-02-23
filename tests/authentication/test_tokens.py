import pytest

from core.authentication.tokens import TokenError, TokenService


class TestAccessToken:
    """Test access token generation and validation."""

    def test_generate_access_token(self, create_user):
        """Access token should be a valid JWT string."""
        user = create_user()
        token = TokenService.generate_access_token(str(user.id), user.role)

        assert isinstance(token, str)
        assert len(token) > 0

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


class TestTokenBlacklist:
    """Test token blacklisting (logout)."""

    def test_blacklist_access_token(self, create_user):
        """Blacklisted token should be rejected."""
        user = create_user()
        token = TokenService.generate_access_token(str(user.id), user.role)

        payload = TokenService.validate_access_token(token)
        assert payload["user_id"] == str(user.id)

        TokenService.blacklist_access_token(token)

        with pytest.raises(TokenError, match="revoked"):
            TokenService.validate_access_token(token)

"""
JWT Token service for Ziona Server.

Handles access token and refresh token generation, validation,
and rotation. Refresh tokens are stored in Redis with TTL.

Security:
- Access tokens: 15min expiry, HS256
- Refresh tokens: 7-day expiry, stored in Redis, rotated on use
- Token blacklisting on logout
"""

import logging
import uuid
from datetime import datetime, timezone

import jwt
from django.conf import settings

logger = logging.getLogger("core.authentication")


class TokenError(Exception):
    """Raised when token operations fail."""

    pass


class TokenService:
    """Service for JWT token operations.

    Handles generating, validating, and revoking JWT tokens.
    Refresh tokens are tracked in Redis for revocation support.
    """

    @staticmethod
    def generate_access_token(user_id: str, role: str) -> str:
        """Generate a short-lived JWT access token.

        Args:
            user_id: UUID of the authenticated user.
            role: User's role (user/admin).

        Returns:
            Encoded JWT access token string.
        """
        now = datetime.now(timezone.utc)
        payload = {
            "user_id": str(user_id),
            "role": role,
            "type": "access",
            "iat": now,
            "exp": now + settings.JWT_ACCESS_TOKEN_LIFETIME,
            "jti": str(uuid.uuid4()),
        }
        return jwt.encode(
            payload,
            settings.JWT_SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )

    @staticmethod
    def generate_refresh_token(user_id: str) -> tuple[str, str]:
        """Generate a long-lived JWT refresh token and store in Redis.

        Args:
            user_id: UUID of the authenticated user.

        Returns:
            Tuple of (encoded refresh token, jti).
        """
        now = datetime.now(timezone.utc)
        jti = str(uuid.uuid4())
        payload = {
            "user_id": str(user_id),
            "type": "refresh",
            "iat": now,
            "exp": now + settings.JWT_REFRESH_TOKEN_LIFETIME,
            "jti": jti,
        }
        token = jwt.encode(
            payload,
            settings.JWT_SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )

        try:
            from django_redis import get_redis_connection

            redis_conn = get_redis_connection("default")
            redis_key = f"refresh:{user_id}:{jti}"
            ttl = int(settings.JWT_REFRESH_TOKEN_LIFETIME.total_seconds())
            redis_conn.setex(redis_key, ttl, "valid")
            logger.info(
                "Refresh token stored in Redis",
                extra={"user_id": str(user_id), "jti": jti},
            )
        except Exception as e:
            logger.warning(f"Failed to store refresh token in Redis: {e}")

        return token, jti

    @staticmethod
    def validate_access_token(token: str) -> dict:
        """Validate and decode a JWT access token.

        Args:
            token: Encoded JWT access token.

        Returns:
            Decoded token payload dict.

        Raises:
            TokenError: If token is invalid, expired, or blacklisted.
        """
        try:
            payload = jwt.decode(
                token,
                settings.JWT_SECRET_KEY,
                algorithms=[settings.JWT_ALGORITHM],
            )
        except jwt.ExpiredSignatureError:
            raise TokenError("Access token has expired") from None
        except jwt.InvalidTokenError as e:
            raise TokenError(f"Invalid access token: {e}") from e

        if payload.get("type") != "access":
            raise TokenError("Token is not an access token")

        try:
            from django_redis import get_redis_connection

            redis_conn = get_redis_connection("default")
            jti = payload.get("jti")
            if jti and redis_conn.exists(f"blacklist:{jti}"):
                raise TokenError("Token has been revoked")
        except TokenError:
            raise
        except Exception:
            logger.debug("Redis unavailable for token blacklist check; allowing token")

        return payload

    @staticmethod
    def validate_refresh_token(token: str) -> dict:
        """Validate a refresh token and check Redis for validity.

        Args:
            token: Encoded JWT refresh token.

        Returns:
            Decoded token payload dict.

        Raises:
            TokenError: If token is invalid, expired, or revoked.
        """
        try:
            payload = jwt.decode(
                token,
                settings.JWT_SECRET_KEY,
                algorithms=[settings.JWT_ALGORITHM],
            )
        except jwt.ExpiredSignatureError:
            raise TokenError("Refresh token has expired") from None
        except jwt.InvalidTokenError as e:
            raise TokenError(f"Invalid refresh token: {e}") from e

        if payload.get("type") != "refresh":
            raise TokenError("Token is not a refresh token")

        try:
            from django_redis import get_redis_connection

            redis_conn = get_redis_connection("default")
            user_id = payload["user_id"]
            jti = payload["jti"]
            redis_key = f"refresh:{user_id}:{jti}"

            if not redis_conn.exists(redis_key):
                raise TokenError("Refresh token has been revoked")
        except TokenError:
            raise
        except Exception as e:
            logger.warning(f"Redis check failed for refresh token: {e}")

        return payload

    @staticmethod
    def rotate_refresh_token(old_token: str, role: str) -> dict:
        """Rotate a refresh token: invalidate old, issue new pair.

        Args:
            old_token: The current refresh token to rotate.
            role: User's current role.

        Returns:
            Dict with new access_token and refresh_token.

        Raises:
            TokenError: If the old token is invalid.
        """
        payload = TokenService.validate_refresh_token(old_token)
        user_id = payload["user_id"]
        old_jti = payload["jti"]

        try:
            from django_redis import get_redis_connection

            redis_conn = get_redis_connection("default")
            redis_conn.delete(f"refresh:{user_id}:{old_jti}")
        except Exception as e:
            logger.warning(f"Failed to revoke old refresh token: {e}")

        access_token = TokenService.generate_access_token(user_id, role)
        refresh_token, new_jti = TokenService.generate_refresh_token(user_id)

        logger.info(
            "Token rotated",
            extra={"user_id": user_id, "old_jti": old_jti, "new_jti": new_jti},
        )

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
        }

    @staticmethod
    def revoke_all_user_tokens(user_id: str) -> None:
        """Revoke all refresh tokens for a user (e.g., on password change).

        Args:
            user_id: UUID of the user whose tokens to revoke.
        """
        try:
            from django_redis import get_redis_connection

            redis_conn = get_redis_connection("default")
            pattern = f"refresh:{user_id}:*"
            keys = redis_conn.keys(pattern)
            if keys:
                redis_conn.delete(*keys)
            logger.info(
                "All tokens revoked",
                extra={"user_id": str(user_id), "count": len(keys)},
            )
        except Exception as e:
            logger.warning(f"Failed to revoke all tokens for user {user_id}: {e}")

    @staticmethod
    def revoke_all_user_tokens_except(user_id: str, keep_jti: str) -> int:
        """Revoke all refresh tokens for a user except the specified one.

        Keeps the current session alive while forcing re-login on
        all other devices.

        Args:
            user_id: UUID of the user.
            keep_jti: JTI of the token to keep (current session).

        Returns:
            Number of tokens revoked.
        """
        try:
            from django_redis import get_redis_connection

            redis_conn = get_redis_connection("default")
            pattern = f"refresh:{user_id}:*"
            keep_key = f"refresh:{user_id}:{keep_jti}"

            keys = redis_conn.keys(pattern)
            keys_to_delete = [k for k in keys if k.decode() != keep_key]

            if keys_to_delete:
                redis_conn.delete(*keys_to_delete)

            revoked = len(keys_to_delete)
            logger.info(
                "Tokens revoked (except current)",
                extra={
                    "user_id": str(user_id),
                    "revoked": revoked,
                    "kept": keep_jti,
                },
            )
            return revoked
        except Exception as e:
            logger.warning(
                "Failed to revoke tokens except current for user %s: %s",
                user_id,
                e,
            )
            return 0

    @staticmethod
    def blacklist_access_token(token: str) -> None:
        """Add an access token to the blacklist (for logout).

        The blacklist entry expires when the token would have expired.

        Args:
            token: The access token to blacklist.
        """
        try:
            payload = jwt.decode(
                token,
                settings.JWT_SECRET_KEY,
                algorithms=[settings.JWT_ALGORITHM],
                options={"verify_exp": False},
            )
            jti = payload.get("jti")
            exp = payload.get("exp", 0)
            now = datetime.now(timezone.utc).timestamp()
            ttl = max(int(exp - now), 1)

            from django_redis import get_redis_connection

            redis_conn = get_redis_connection("default")
            redis_conn.setex(f"blacklist:{jti}", ttl, "revoked")
            logger.info("Access token blacklisted", extra={"jti": jti})
        except Exception as e:
            logger.warning(f"Failed to blacklist access token: {e}")

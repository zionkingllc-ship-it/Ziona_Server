"""JWT Token service for Ziona Server.

Handles access token and refresh token generation, validation,
and rotation. Refresh tokens are stored in Redis with TTL.

Security model:
- Access tokens:  24 hour expiry, HS256 signed, CPU-validated only.
- Refresh tokens: 30-day expiry, stored in Redis, rotated on every use.
- Refresh rotation keeps a brief replay grace value so duplicate mobile
  refresh requests return the same rotated token pair instead of failing.
- On logout: refresh token is immediately revoked in Redis (no new tokens
  issued), and the access token is blacklisted with its remaining TTL.
  Since access tokens live 24 hours, the blacklist entry may be up to
  24 hours wide — after which the token is independently expired.

WHY WE DO NOT CHECK THE BLACKLIST ON EVERY REQUEST:
  Checking Redis on every authenticated call costs 1 command/request and
  contributed to exhausting our Upstash request budget. With a 24-hour
  access token TTL this check is unnecessary: the worst-case exposure
  window after a logout/compromise is 24 hours, which is the
  tradeoff OAuth 2.0 RFC 6749 accepts by design. The refresh token is
  revoked immediately, preventing any new access tokens from being issued.
"""

import json
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

        Validation is performed entirely in CPU (JWT signature + expiry check).
        No Redis call is made. See module docstring for security rationale.

        Args:
            token: Encoded JWT access token.

        Returns:
            Decoded token payload dict.

        Raises:
            TokenError: If token is invalid or expired.
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
            old_key = f"refresh:{user_id}:{old_jti}"
            existing_value = redis_conn.get(old_key)
            cached_rotation = _decode_rotation_value(existing_value)
            if cached_rotation:
                logger.info(
                    "Refresh token replay grace used",
                    extra={"user_id": user_id, "old_jti": old_jti},
                )
                return cached_rotation
        except Exception as e:
            logger.warning(f"Failed to inspect old refresh token: {e}")

        access_token = TokenService.generate_access_token(user_id, role)
        refresh_token, new_jti = TokenService.generate_refresh_token(user_id)

        try:
            from django_redis import get_redis_connection

            redis_conn = get_redis_connection("default")
            old_key = f"refresh:{user_id}:{old_jti}"
            grace_seconds = max(int(settings.JWT_REFRESH_ROTATION_GRACE_SECONDS), 0)
            if grace_seconds > 0:
                redis_conn.setex(
                    old_key,
                    grace_seconds,
                    json.dumps(
                        {
                            "access_token": access_token,
                            "refresh_token": refresh_token,
                        }
                    ),
                )
            else:
                redis_conn.delete(old_key)
        except Exception as e:
            logger.warning(f"Failed to store refresh rotation grace: {e}")

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
            keys = list(_scan_redis_keys(redis_conn, pattern))
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

            keys = list(_scan_redis_keys(redis_conn, pattern))
            keys_to_delete = [key for key in keys if _decode_redis_key(key) != keep_key]

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


def _decode_rotation_value(value) -> dict | None:
    """Return cached rotated token pair when a refresh token is replayed briefly."""
    if not value:
        return None
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if value == "valid":
        return None
    try:
        data = json.loads(value)
    except (TypeError, ValueError):
        return None
    if data.get("access_token") and data.get("refresh_token"):
        return {
            "access_token": data["access_token"],
            "refresh_token": data["refresh_token"],
        }
    return None


def _scan_redis_keys(redis_conn, pattern: str):
    """Yield Redis keys without using KEYS, which is costly and blocking at scale."""
    yield from redis_conn.scan_iter(match=pattern, count=100)


def _decode_redis_key(key) -> str:
    """Normalize redis-py bytes/str keys for comparisons."""
    if isinstance(key, bytes):
        return key.decode("utf-8")
    return str(key)

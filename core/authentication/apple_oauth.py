"""Sign in with Apple identity-token verification helpers.

This module performs local JWT validation against Apple's rotating public keys.
It never exchanges authorization codes with Apple during login; the client sends
the `identityToken`, and the backend verifies signature, issuer, audience,
expiry, subject, and nonce before creating or logging in a Ziona user.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import threading
from typing import Any

import jwt
import requests
from django.conf import settings
from django.core.cache import cache
from jwt import (
    ExpiredSignatureError,
    InvalidAudienceError,
    InvalidIssuerError,
    InvalidSignatureError,
    PyJWTError,
)
from jwt.algorithms import RSAAlgorithm

from core.authentication.validators import AuthenticationError

logger = logging.getLogger("core.authentication")

_APPLE_KEYS_LOCK = threading.RLock()
_APPLE_NONCE_CACHE_PREFIX = "apple_signin_nonce"


def create_apple_nonce() -> dict[str, Any]:
    """Create a server-issued nonce challenge for Sign in with Apple.

    Mobile flow:
    1. Call `/api/auth/apple/nonce`.
    2. Send `nonce` to Apple as the request nonce.
    3. Send `identityToken` and `rawNonce` back to `/api/auth/apple`.

    The backend hashes `rawNonce` and compares it to the token's `nonce` claim.
    """
    raw_nonce = secrets.token_urlsafe(32)
    nonce = _sha256(raw_nonce)
    ttl = int(settings.APPLE_NONCE_TTL_SECONDS)
    cache.set(_nonce_cache_key(nonce), True, timeout=ttl)
    return {
        "rawNonce": raw_nonce,
        "nonce": nonce,
        "expiresIn": ttl,
    }


def verify_apple_identity_token(
    identity_token: str,
    *,
    nonce: str | None = None,
    raw_nonce: str | None = None,
) -> dict[str, Any]:
    """Verify an Apple identity token and return its claims.

    Raises:
        AuthenticationError: for all caller-facing validation failures.
    """
    if not identity_token:
        raise AuthenticationError("Apple identity token is required", code="MISSING_FIELDS")

    allowed_audiences = _get_apple_client_ids()
    if not allowed_audiences:
        logger.error("Apple OAuth rejected because no client IDs are configured")
        raise AuthenticationError(
            "Apple authentication is not configured",
            code="OAUTH_NOT_CONFIGURED",
        )

    expected_nonce = _expected_nonce(nonce=nonce, raw_nonce=raw_nonce)

    try:
        header = jwt.get_unverified_header(identity_token)
    except PyJWTError as exc:
        logger.warning("Apple token rejected because header could not be decoded")
        raise AuthenticationError(
            "Invalid Apple authentication token",
            code="INVALID_OAUTH_TOKEN",
        ) from exc

    if header.get("alg") != "RS256":
        logger.warning(
            "Apple token rejected due to unexpected algorithm",
            extra={"alg": header.get("alg")},
        )
        raise AuthenticationError(
            "Invalid Apple authentication token",
            code="INVALID_OAUTH_TOKEN",
        )

    kid = header.get("kid")
    public_key = _get_public_key_for_kid(kid)

    try:
        claims = jwt.decode(
            identity_token,
            key=public_key,
            algorithms=["RS256"],
            audience=allowed_audiences,
            issuer=settings.APPLE_ID_TOKEN_ISSUER,
            options={"require": ["iss", "aud", "exp", "iat", "sub"]},
        )
    except ExpiredSignatureError as exc:
        raise AuthenticationError(
            "Apple identity token has expired",
            code="APPLE_TOKEN_EXPIRED",
        ) from exc
    except InvalidAudienceError as exc:
        logger.warning("Apple token rejected due to unexpected audience")
        raise AuthenticationError(
            "Invalid Apple token audience",
            code="INVALID_OAUTH_TOKEN",
        ) from exc
    except InvalidIssuerError as exc:
        logger.warning("Apple token rejected due to invalid issuer")
        raise AuthenticationError(
            "Invalid Apple authentication token",
            code="INVALID_OAUTH_TOKEN",
        ) from exc
    except InvalidSignatureError as exc:
        logger.warning("Apple token rejected due to invalid signature")
        raise AuthenticationError(
            "Invalid Apple authentication token signature",
            code="INVALID_OAUTH_TOKEN",
        ) from exc
    except PyJWTError as exc:
        logger.warning("Apple token rejected during JWT validation", exc_info=True)
        raise AuthenticationError(
            "Invalid Apple authentication token",
            code="INVALID_OAUTH_TOKEN",
        ) from exc

    _verify_nonce_claim(claims, expected_nonce)
    return claims


def _get_apple_client_ids() -> list[str]:
    client_ids = getattr(settings, "APPLE_CLIENT_IDS", None)
    if client_ids:
        return [client_id for client_id in client_ids if client_id]

    legacy_client_id = getattr(settings, "APPLE_CLIENT_ID", "")
    return [legacy_client_id] if legacy_client_id else []


def _get_public_key_for_kid(kid: str | None):
    if not kid:
        raise AuthenticationError(
            "Invalid Apple authentication token",
            code="INVALID_OAUTH_TOKEN",
        )

    key = _find_key(kid, _get_apple_public_keys())
    if key is None:
        key = _find_key(kid, _get_apple_public_keys(force_refresh=True))

    if key is None:
        logger.warning("Apple public key not found for token kid", extra={"kid": kid})
        raise AuthenticationError(
            "Unable to verify Apple authentication token",
            code="APPLE_PUBLIC_KEY_NOT_FOUND",
        )

    return RSAAlgorithm.from_jwk(json.dumps(key))


def _get_apple_public_keys(*, force_refresh: bool = False) -> list[dict[str, Any]]:
    cache_key = settings.APPLE_PUBLIC_KEYS_CACHE_KEY
    if not force_refresh:
        cached = cache.get(cache_key)
        if cached:
            return cached

    with _APPLE_KEYS_LOCK:
        if not force_refresh:
            cached = cache.get(cache_key)
            if cached:
                return cached

        try:
            response = requests.get(
                settings.APPLE_PUBLIC_KEYS_URL,
                timeout=settings.APPLE_PUBLIC_KEYS_REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.Timeout as exc:
            logger.error("Timed out fetching Apple public keys")
            raise AuthenticationError(
                "Apple authentication is temporarily unavailable",
                code="APPLE_KEYS_TIMEOUT",
            ) from exc
        except requests.RequestException as exc:
            logger.error("Failed to fetch Apple public keys", exc_info=True)
            raise AuthenticationError(
                "Apple authentication is temporarily unavailable",
                code="APPLE_KEYS_UNAVAILABLE",
            ) from exc
        except ValueError as exc:
            logger.error("Apple public keys response was not valid JSON")
            raise AuthenticationError(
                "Apple authentication is temporarily unavailable",
                code="APPLE_KEYS_INVALID",
            ) from exc

        keys = payload.get("keys")
        if not isinstance(keys, list) or not keys:
            logger.error("Apple public keys response did not include keys")
            raise AuthenticationError(
                "Apple authentication is temporarily unavailable",
                code="APPLE_KEYS_INVALID",
            )

        cache.set(cache_key, keys, timeout=settings.APPLE_PUBLIC_KEYS_CACHE_TIMEOUT)
        return keys


def _find_key(kid: str, keys: list[dict[str, Any]]) -> dict[str, Any] | None:
    for key in keys:
        if key.get("kid") == kid:
            return key
    return None


def _expected_nonce(*, nonce: str | None, raw_nonce: str | None) -> str:
    if raw_nonce:
        return _sha256(raw_nonce.strip())
    if nonce:
        return nonce.strip()
    raise AuthenticationError(
        "Apple nonce is required",
        code="APPLE_NONCE_REQUIRED",
    )


def _verify_nonce_claim(claims: dict[str, Any], expected_nonce: str) -> None:
    token_nonce = claims.get("nonce")
    if not token_nonce:
        raise AuthenticationError(
            "Apple identity token is missing nonce",
            code="APPLE_NONCE_REQUIRED",
        )
    if token_nonce != expected_nonce:
        raise AuthenticationError(
            "Invalid Apple nonce",
            code="APPLE_NONCE_MISMATCH",
        )

    if settings.APPLE_REQUIRE_SERVER_NONCE:
        cache_key = _nonce_cache_key(expected_nonce)
        if not cache.get(cache_key):
            raise AuthenticationError(
                "Apple nonce has expired or was already used",
                code="APPLE_NONCE_EXPIRED",
            )
        cache.delete(cache_key)


def _nonce_cache_key(nonce: str) -> str:
    return f"{_APPLE_NONCE_CACHE_PREFIX}:{nonce}"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

"""
OAuth service — Google OAuth authentication.

Handles Google ID token verification and user creation/linking.
"""

import logging
from typing import Any

from django.conf import settings
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

from core.authentication.account_status import ensure_account_can_authenticate
from core.authentication.activity import record_successful_auth
from core.authentication.tokens import TokenService
from core.authentication.validators import AuthenticationError
from core.shared.logging import log_security_event
from core.users.models import User

logger = logging.getLogger("core.authentication")


class OAuthService:
    """Service handling OAuth authentication."""

    @staticmethod
    def google_oauth_login(
        id_token: str,
        ip_address: str | None = None,
    ) -> dict[str, Any]:
        """Authenticate or register a user via Google OAuth.

        Verifies the Google ID token, creates user if new,
        and returns JWT tokens alongside user data.

        Args:
            id_token: Google ID token from client.
            ip_address: Client IP for audit logging.

        Returns:
            Dict with user data, access_token, refresh_token, and is_new_user flag.

        Raises:
            AuthenticationError: If token verification or account binding fails.
        """
        allowed_client_ids = _get_google_client_ids()
        if not allowed_client_ids:
            logger.error("Google OAuth rejected because no client IDs are configured")
            raise AuthenticationError(
                "Google authentication is not configured",
                code="OAUTH_NOT_CONFIGURED",
            )

        try:
            google_user_info = google_id_token.verify_oauth2_token(
                id_token,
                google_requests.Request(),
                None,
            )

            if google_user_info.get("aud") not in allowed_client_ids:
                logger.warning(
                    "Google token rejected due to unexpected audience",
                    extra={"audience": google_user_info.get("aud")},
                )
                raise AuthenticationError(
                    "Invalid Google token audience",
                    code="INVALID_OAUTH_TOKEN",
                )

            email = google_user_info.get("email")
            google_id = google_user_info.get("sub")
            is_verified = google_user_info.get("email_verified", False)

            if not email or not google_id:
                raise AuthenticationError(
                    "Invalid Google token - missing email or ID",
                    code="INVALID_OAUTH_TOKEN",
                )

        except ValueError as e:
            logger.error(f"Google token verification failed: {e}")
            raise AuthenticationError(
                "Invalid Google authentication token",
                code="INVALID_OAUTH_TOKEN",
            ) from e

        existing_user = User.all_objects.filter(email=email).first()
        is_new_user = False

        if existing_user:
            ensure_account_can_authenticate(existing_user)

            if existing_user.social_auth_provider is None:
                raise AuthenticationError(
                    "This email is already registered with a password. Please sign in with your password instead, or use 'Forgot Password' to reset it.",
                    code="EMAIL_REGISTERED_WITH_PASSWORD",
                )

            if existing_user.social_auth_provider != "google":
                raise AuthenticationError(
                    f"This email is already registered via {existing_user.social_auth_provider}. "
                    f"Please sign in with {existing_user.social_auth_provider} instead.",
                    code="EMAIL_REGISTERED_WITH_DIFFERENT_PROVIDER",
                )

            user = existing_user

            if not user.google_id:
                user.google_id = google_id
                user.save(update_fields=["google_id"])
        else:
            import secrets

            temp_username = f"user_{secrets.token_hex(4)}"
            name = google_user_info.get("name", "")
            picture = google_user_info.get("picture", "")

            user = User.objects.create_user(
                email=email,
                username=temp_username,
                full_name=name,
                avatar_url=picture,
                is_email_verified=is_verified,
                needs_username_selection=True,
                social_auth_provider="google",
                google_id=google_id,
                last_login_ip=ip_address,
            )

            user.set_unusable_password()
            user.save()
            is_new_user = True

        record_successful_auth(user, ip_address)

        access_token = TokenService.generate_access_token(str(user.id), user.role)
        refresh_token, _ = TokenService.generate_refresh_token(str(user.id))

        log_security_event(
            "auth.oauth.success",
            user_id=str(user.id),
            ip_address=ip_address,
            metadata={"provider": "google", "is_new_user": is_new_user},
        )

        return {
            "user": user,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "is_new_user": is_new_user,
        }

    @staticmethod
    def apple_oauth_login(
        identity_token: str,
        nonce: str | None = None,
        raw_nonce: str | None = None,
        apple_user: dict[str, Any] | None = None,
        ip_address: str | None = None,
    ) -> dict[str, Any]:
        """Authenticate or register a user via Sign in with Apple.

        Apple identifies users by the stable `sub` claim. Name information is
        only sent on the first authorization callback, so subsequent logins are
        linked by `apple_sub` and do not require profile fields.
        """
        from core.authentication.apple_oauth import verify_apple_identity_token

        claims = verify_apple_identity_token(
            identity_token,
            nonce=nonce,
            raw_nonce=raw_nonce,
        )

        apple_sub = claims.get("sub")
        if not apple_sub:
            raise AuthenticationError(
                "Invalid Apple token - missing subject",
                code="INVALID_OAUTH_TOKEN",
            )

        apple_user = apple_user or {}
        email = _apple_email_from_claims_or_user(claims, apple_user)
        full_name = _apple_full_name(apple_user)
        email_verified = _apple_email_verified(claims.get("email_verified"))

        user = User.all_objects.filter(apple_sub=apple_sub).first()
        is_new_user = False

        if user:
            ensure_account_can_authenticate(user)
        else:
            if not email:
                raise AuthenticationError(
                    "Apple did not provide an email for this first sign-in. Please retry Apple authorization and share your email.",
                    code="APPLE_EMAIL_REQUIRED",
                )

            existing_user = User.all_objects.filter(email=email).first()
            if existing_user:
                ensure_account_can_authenticate(existing_user)

                if existing_user.social_auth_provider is None:
                    raise AuthenticationError(
                        "This email is already registered with a password. Please sign in with your password instead, or use 'Forgot Password' to reset it.",
                        code="EMAIL_REGISTERED_WITH_PASSWORD",
                    )

                if existing_user.social_auth_provider != "apple":
                    raise AuthenticationError(
                        f"This email is already registered via {existing_user.social_auth_provider}. "
                        f"Please sign in with {existing_user.social_auth_provider} instead.",
                        code="EMAIL_REGISTERED_WITH_DIFFERENT_PROVIDER",
                    )

                if existing_user.apple_sub and existing_user.apple_sub != apple_sub:
                    raise AuthenticationError(
                        "This email is already linked to a different Apple account.",
                        code="APPLE_ACCOUNT_MISMATCH",
                    )

                user = existing_user
                user.apple_sub = apple_sub
                update_fields = ["apple_sub", "updated_at"]
                if full_name and not user.full_name:
                    user.full_name = full_name
                    update_fields.append("full_name")
                user.save(update_fields=update_fields)
            else:
                import secrets

                temp_username = f"user_{secrets.token_hex(4)}"
                user = User.objects.create_user(
                    email=email,
                    username=temp_username,
                    full_name=full_name,
                    is_email_verified=email_verified,
                    needs_username_selection=True,
                    auth_provider="apple",
                    social_auth_provider="apple",
                    apple_sub=apple_sub,
                    last_login_ip=ip_address,
                )
                user.set_unusable_password()
                user.save()
                is_new_user = True

        record_successful_auth(user, ip_address)

        access_token = TokenService.generate_access_token(str(user.id), user.role)
        refresh_token, _ = TokenService.generate_refresh_token(str(user.id))

        log_security_event(
            "auth.oauth.success",
            user_id=str(user.id),
            ip_address=ip_address,
            metadata={"provider": "apple", "is_new_user": is_new_user},
        )

        return {
            "user": user,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "is_new_user": is_new_user,
        }


def _get_google_client_ids() -> list[str]:
    """Return configured first-party Google client IDs accepted by the backend."""
    client_ids = getattr(settings, "GOOGLE_CLIENT_IDS", None)
    if client_ids:
        return [client_id for client_id in client_ids if client_id]

    legacy_client_id = getattr(settings, "GOOGLE_CLIENT_ID", "")
    return [legacy_client_id] if legacy_client_id else []


def _apple_email_from_claims_or_user(claims: dict[str, Any], apple_user: dict[str, Any]) -> str:
    email = claims.get("email") or apple_user.get("email") or ""
    return email.lower().strip()


def _apple_full_name(apple_user: dict[str, Any]) -> str:
    name = apple_user.get("name") or {}
    if isinstance(name, dict):
        parts = [
            name.get("firstName") or name.get("first_name") or "",
            name.get("lastName") or name.get("last_name") or "",
        ]
        return " ".join(part.strip() for part in parts if part and part.strip())

    if isinstance(name, str):
        return name.strip()

    first_name = apple_user.get("firstName") or apple_user.get("first_name") or ""
    last_name = apple_user.get("lastName") or apple_user.get("last_name") or ""
    return " ".join(part.strip() for part in [first_name, last_name] if part and part.strip())


def _apple_email_verified(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() == "true"
    return bool(value)

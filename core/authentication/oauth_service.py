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

        user.last_login_ip = ip_address
        user.save(update_fields=["last_login_ip", "updated_at"])

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


def _get_google_client_ids() -> list[str]:
    """Return configured first-party Google client IDs accepted by the backend."""
    client_ids = getattr(settings, "GOOGLE_CLIENT_IDS", None)
    if client_ids:
        return [client_id for client_id in client_ids if client_id]

    legacy_client_id = getattr(settings, "GOOGLE_CLIENT_ID", "")
    return [legacy_client_id] if legacy_client_id else []

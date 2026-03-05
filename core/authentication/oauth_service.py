"""
OAuth service — Google OAuth authentication.

Handles Firebase ID token verification and user creation/linking.
"""

import logging
from typing import Any

from core.authentication.tokens import TokenService
from core.authentication.validators import AuthenticationError, generate_unique_username
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

        Verifies the Firebase ID token, creates user if new,
        and returns JWT tokens.

        Args:
            id_token: Firebase ID token from client.
            ip_address: Client IP for audit logging.

        Returns:
            Dict with user data, access_token, refresh_token, and is_new_user flag.

        Raises:
            AuthenticationError: If token verification fails.
        """
        try:
            from firebase_admin import auth as firebase_auth

            decoded_token = firebase_auth.verify_id_token(id_token)
        except Exception as e:
            logger.error(f"Firebase token verification failed: {e}")
            raise AuthenticationError(
                "Invalid Google authentication token",
                code="INVALID_OAUTH_TOKEN",
            ) from e

        firebase_uid = decoded_token["uid"]
        email = decoded_token.get("email", "")
        name = decoded_token.get("name", "")
        picture = decoded_token.get("picture", "")

        is_new_user = False

        try:
            user = User.objects.get(firebase_uid=firebase_uid)
        except User.DoesNotExist:
            try:
                user = User.objects.get(email=email)
                user.firebase_uid = firebase_uid
                user.auth_provider = "google"
                user.is_email_verified = True
                user.save(
                    update_fields=[
                        "firebase_uid",
                        "auth_provider",
                        "is_email_verified",
                        "updated_at",
                    ]
                )
            except User.DoesNotExist:
                username = generate_unique_username(email, name)
                user = User.objects.create_user(
                    email=email,
                    username=username,
                    full_name=name,
                    avatar_url=picture,
                    firebase_uid=firebase_uid,
                    auth_provider="google",
                    is_email_verified=True,
                    last_login_ip=ip_address,
                )
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

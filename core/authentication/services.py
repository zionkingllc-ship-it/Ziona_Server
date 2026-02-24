"""
Authentication services for Ziona Server.

Business logic layer for user registration, login, email verification,
password reset, and Google OAuth. Follows the Service Layer Pattern:
Resolver/View → Service → Selector → ORM
"""

import logging
import secrets
import string
from datetime import UTC
from typing import Any

from django.conf import settings

from core.authentication.tokens import TokenError, TokenService
from core.shared.logging import log_security_event, mask_email
from core.users.models import User

logger = logging.getLogger("core.authentication")


class AuthenticationError(Exception):
    """Raised when authentication operations fail."""

    def __init__(self, message: str, code: str = "AUTH_ERROR"):
        self.message = message
        self.code = code
        super().__init__(message)


class AuthService:
    """Service handling all authentication business logic.

    Methods:
        register: Create a new user with email/password
        login: Authenticate user and return JWT tokens
        verify_email: Verify user's email with token
        request_password_reset: Send OTP to user's email
        reset_password: Reset password using OTP
        google_oauth_login: Authenticate via Google OAuth
        refresh_tokens: Rotate refresh token for new token pair
        logout: Revoke user's tokens
    """

    @staticmethod
    def register(
        email: str,
        password: str,
        full_name: str = "",
        ip_address: str | None = None,
    ) -> dict[str, Any]:
        """Register a new user with email and password.

        Username is NOT required at registration — it is set later
        during onboarding (matching the Figma flow: email → password →
        username → birthday).

        Args:
            email: User's email address.
            password: Plain text password (8-20 chars, complexity enforced).
            full_name: Optional full name.
            ip_address: Registration IP for audit logging.

        Returns:
            Dict with user data, access_token, and refresh_token.

        Raises:
            AuthenticationError: If email taken or password invalid.
        """
        import uuid as _uuid

        email = email.lower().strip()

        # Validate email uniqueness
        if User.objects.filter(email=email).exists():
            raise AuthenticationError(
                "An account with this email already exists",
                code="EMAIL_EXISTS",
            )

        # Validate password complexity
        _validate_password(password)

        # Generate temporary username (user sets real one in onboarding)
        temp_username = f"user_{_uuid.uuid4().hex[:8]}"

        # Create user
        user = User.objects.create_user(
            email=email,
            username=temp_username,
            password=password,
            full_name=full_name,
            last_login_ip=ip_address,
        )

        # Send verification email
        AuthService._send_verification_email(user)

        # Generate tokens
        access_token = TokenService.generate_access_token(str(user.id), user.role)
        refresh_token, _ = TokenService.generate_refresh_token(str(user.id))

        log_security_event(
            "auth.register.success",
            user_id=str(user.id),
            ip_address=ip_address,
            metadata={"email": mask_email(email)},
        )

        return {
            "user": user,
            "access_token": access_token,
            "refresh_token": refresh_token,
        }

    @staticmethod
    def login(
        email: str,
        password: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict[str, Any]:
        """Authenticate user with email and password.

        Args:
            email: User's email address.
            password: Plain text password.
            ip_address: Client IP for audit logging.
            user_agent: Client User-Agent for audit logging.

        Returns:
            Dict with user data, access_token, and refresh_token.

        Raises:
            AuthenticationError: If credentials invalid or email not verified.
        """
        email = email.lower().strip()

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            log_security_event(
                "auth.login.failed",
                ip_address=ip_address,
                metadata={"reason": "user_not_found", "email": mask_email(email)},
            )
            raise AuthenticationError(
                "Invalid email or password",
                code="INVALID_CREDENTIALS",
            ) from None

        if not user.check_password(password):
            log_security_event(
                "auth.login.failed",
                user_id=str(user.id),
                ip_address=ip_address,
                user_agent=user_agent,
                metadata={"reason": "invalid_password"},
            )
            raise AuthenticationError(
                "Invalid email or password",
                code="INVALID_CREDENTIALS",
            )

        if not user.is_email_verified:
            raise AuthenticationError(
                "Please verify your email before logging in",
                code="EMAIL_NOT_VERIFIED",
            )

        if not user.is_active:
            raise AuthenticationError(
                "This account has been deactivated",
                code="ACCOUNT_DEACTIVATED",
            )

        # Update last login
        user.last_login_ip = ip_address
        user.save(update_fields=["last_login_ip", "updated_at"])

        # Generate tokens
        access_token = TokenService.generate_access_token(str(user.id), user.role)
        refresh_token, _ = TokenService.generate_refresh_token(str(user.id))

        log_security_event(
            "auth.login.success",
            user_id=str(user.id),
            ip_address=ip_address,
            user_agent=user_agent,
        )

        return {
            "user": user,
            "access_token": access_token,
            "refresh_token": refresh_token,
        }

    @staticmethod
    def verify_email(token: str) -> bool:
        """Verify a user's email address using a verification token.

        Args:
            token: JWT verification token from email link.

        Returns:
            True if verification succeeded.

        Raises:
            AuthenticationError: If token is invalid or expired.
        """
        try:
            import jwt as pyjwt

            payload = pyjwt.decode(
                token,
                settings.JWT_SECRET_KEY,
                algorithms=[settings.JWT_ALGORITHM],
            )
        except Exception:
            raise AuthenticationError(
                "Invalid or expired verification link",
                code="INVALID_VERIFICATION_TOKEN",
            ) from None

        if payload.get("type") != "email_verification":
            raise AuthenticationError(
                "Invalid verification token",
                code="INVALID_VERIFICATION_TOKEN",
            )

        try:
            user = User.objects.get(id=payload["user_id"])
        except User.DoesNotExist:
            raise AuthenticationError(
                "User not found",
                code="USER_NOT_FOUND",
            ) from None

        user.is_email_verified = True
        user.save(update_fields=["is_email_verified", "updated_at"])

        log_security_event(
            "auth.email_verified",
            user_id=str(user.id),
        )

        return True

    @staticmethod
    def request_password_reset(
        email: str,
        ip_address: str | None = None,
    ) -> bool:
        """Send a password reset OTP to the user's email.

        Args:
            email: User's email address.
            ip_address: Client IP for audit logging.

        Returns:
            True (always, to prevent email enumeration).
        """
        email = email.lower().strip()

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            # Return True to prevent email enumeration
            return True

        # Generate 6-digit OTP
        otp = "".join(secrets.choice(string.digits) for _ in range(6))

        # Store OTP in Redis (10min TTL)
        try:
            from django_redis import get_redis_connection

            redis_conn = get_redis_connection("default")
            redis_key = f"otp:password_reset:{user.id}"
            redis_conn.setex(redis_key, 600, otp)  # 10 minutes
        except Exception as e:
            logger.error(f"Failed to store OTP in Redis: {e}")
            raise AuthenticationError(
                "Failed to send reset code. Please try again.",
                code="OTP_STORAGE_FAILED",
            ) from e

        # Send email with OTP (async via Celery - non-blocking)
        from core.shared.tasks.email_tasks import send_email_async

        send_email_async.delay(
            subject="Ziona - Password Reset Code",
            message=f"Your password reset code is: {otp}\n\nThis code expires in 10 minutes.",
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
        )

        log_security_event(
            "auth.password_reset.requested",
            user_id=str(user.id),
            ip_address=ip_address,
            metadata={"email": mask_email(email)},
        )

        return True

    @staticmethod
    def reset_password(
        email: str,
        otp: str,
        new_password: str,
        ip_address: str | None = None,
    ) -> bool:
        """Reset user's password using OTP.

        Args:
            email: User's email address.
            otp: 6-digit OTP from email.
            new_password: New password (complexity enforced).
            ip_address: Client IP for audit logging.

        Returns:
            True if password was reset successfully.

        Raises:
            AuthenticationError: If OTP is invalid or password doesn't meet requirements.
        """
        email = email.lower().strip()

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            raise AuthenticationError(
                "Invalid email or OTP",
                code="INVALID_OTP",
            ) from None

        # Validate OTP from Redis
        try:
            from django_redis import get_redis_connection

            redis_conn = get_redis_connection("default")
            redis_key = f"otp:password_reset:{user.id}"
            stored_otp = redis_conn.get(redis_key)

            if stored_otp is None:
                raise AuthenticationError(
                    "Reset code has expired. Please request a new one.",
                    code="OTP_EXPIRED",
                )

            if stored_otp.decode() != otp:
                raise AuthenticationError(
                    "Invalid reset code",
                    code="INVALID_OTP",
                )

            # OTP is valid - delete it
            redis_conn.delete(redis_key)

        except AuthenticationError:
            raise
        except Exception as e:
            logger.error(f"OTP validation failed: {e}")
            raise AuthenticationError(
                "Failed to validate reset code. Please try again.",
                code="OTP_VALIDATION_FAILED",
            ) from e

        # Validate and set new password
        _validate_password(new_password)
        user.set_password(new_password)
        user.save(update_fields=["password", "updated_at"])

        # Revoke all existing tokens
        TokenService.revoke_all_user_tokens(str(user.id))

        log_security_event(
            "auth.password_reset.success",
            user_id=str(user.id),
            ip_address=ip_address,
        )

        return True

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

        # Check if user exists by Firebase UID
        try:
            user = User.objects.get(firebase_uid=firebase_uid)
        except User.DoesNotExist:
            # Check if user exists by email
            try:
                user = User.objects.get(email=email)
                # Link existing email account to Google
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
                # Create new user
                username = _generate_unique_username(email, name)
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

        # Update last login
        user.last_login_ip = ip_address
        user.save(update_fields=["last_login_ip", "updated_at"])

        # Generate tokens
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
    def refresh_tokens(refresh_token: str) -> dict[str, str]:
        """Rotate a refresh token for a new access/refresh token pair.

        Args:
            refresh_token: Current refresh token.

        Returns:
            Dict with new access_token and refresh_token.

        Raises:
            AuthenticationError: If refresh token is invalid.
        """
        try:
            # Get user's current role
            payload = TokenService.validate_refresh_token(refresh_token)
            user_id = payload["user_id"]
            try:
                user = User.objects.get(id=user_id)
                role = user.role
            except User.DoesNotExist:
                raise TokenError("User not found") from None
            return TokenService.rotate_refresh_token(refresh_token, role)
        except TokenError as e:
            raise AuthenticationError(str(e), code="INVALID_REFRESH_TOKEN") from e

    @staticmethod
    def logout(
        access_token: str,
        refresh_token: str | None = None,
        user_id: str | None = None,
    ) -> bool:
        """Log out a user by revoking their tokens.

        Args:
            access_token: Current access token to blacklist.
            refresh_token: Current refresh token to revoke.
            user_id: User ID for audit logging.

        Returns:
            True if logout succeeded.
        """
        # Blacklist the access token
        TokenService.blacklist_access_token(access_token)

        # Revoke the refresh token
        if refresh_token:
            try:
                payload = TokenService.validate_refresh_token(refresh_token)
                from django_redis import get_redis_connection

                redis_conn = get_redis_connection("default")
                uid = payload["user_id"]
                jti = payload["jti"]
                redis_conn.delete(f"refresh:{uid}:{jti}")
            except Exception:
                logger.debug("Failed to revoke refresh token on logout", exc_info=True)

        log_security_event(
            "auth.logout",
            user_id=user_id,
        )

        return True

    @staticmethod
    def _send_verification_email(user: User) -> None:
        """Send email verification link to user (async via Celery).

        Args:
            user: User instance to send verification to.
        """
        from datetime import datetime, timedelta

        import jwt as pyjwt

        from core.shared.tasks.email_tasks import send_email_async

        token = pyjwt.encode(
            {
                "user_id": str(user.id),
                "type": "email_verification",
                "exp": datetime.now(UTC) + timedelta(hours=24),
            },
            settings.JWT_SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )

        # Build verification URL (frontend handles this)
        verification_url = f"https://ziona.app/verify-email?token={token}"

        # Send email asynchronously via Celery (non-blocking)
        send_email_async.delay(
            subject="Welcome to Ziona - Verify Your Email",
            message=(
                f"Hi {user.full_name or 'there'},\n\n"
                f"Welcome to Ziona! Please verify your email by clicking:\n"
                f"{verification_url}\n\n"
                f"This link expires in 24 hours.\n\n"
                f"- The Ziona Team"
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
        )


def _validate_password(password: str) -> None:
    """Validate password meets Figma design requirements.

    Requirements (from Figma):
    - 8-20 characters
    - At least 1 letter (uppercase OR lowercase)
    - At least 1 number
    - At least 1 special character (! @ # ? etc.)

    Args:
        password: Plain text password to validate.

    Raises:
        AuthenticationError: If password doesn't meet requirements.
    """
    if len(password) < 8 or len(password) > 20:
        raise AuthenticationError(
            "Password must be 8-20 characters",
            code="PASSWORD_LENGTH_INVALID",
        )
    if not any(c.isalpha() for c in password):
        raise AuthenticationError(
            "Password must contain at least one letter",
            code="PASSWORD_NO_LETTER",
        )
    if not any(c.isdigit() for c in password):
        raise AuthenticationError(
            "Password must contain at least one number",
            code="PASSWORD_NO_NUMBER",
        )
    if not any(c in "!@#$%^&*()_+-=[]{}|;:,.<>?" for c in password):
        raise AuthenticationError(
            "Password must contain at least one special character",
            code="PASSWORD_NO_SPECIAL",
        )


def _generate_unique_username(email: str, name: str) -> str:
    """Generate a unique username from email or name.

    Args:
        email: User's email address.
        name: User's full name.

    Returns:
        A unique username string.
    """
    import re

    # Try name first
    if name:
        base = re.sub(r"[^a-zA-Z0-9_]", "", name.lower().replace(" ", "_"))
    else:
        base = email.split("@")[0]
        base = re.sub(r"[^a-zA-Z0-9_]", "", base)

    base = base[:20]  # Leave room for suffix

    username = base
    counter = 1
    while User.all_objects.filter(username=username).exists():
        username = f"{base}_{counter}"
        counter += 1

    return username

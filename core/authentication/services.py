"""
Authentication services for Ziona Server.

Business logic layer for user registration, login, email verification (OTP),
password reset, and Google OAuth. Follows the Service Layer Pattern:
Resolver/View → Service → Selector → ORM
"""

import logging
import random
import re
import secrets
import string
from datetime import date, datetime, timezone
from typing import Any

from cryptography.fernet import Fernet
from django.conf import settings
from django.db import IntegrityError

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
        register: Create a new user with email/password/username/DOB
        login: Authenticate user and return JWT tokens
        send_verification_otp: Send 6-digit OTP to verify email
        verify_email_otp: Verify email with OTP and return tokens
        resend_verification_otp: Resend OTP with rate limiting
        suggest_usernames: Generate unique username suggestions
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
        username: str,
        date_of_birth: str,
        ip_address: str | None = None,
    ) -> dict[str, Any]:
        """Register a new user with email, password, username, and DOB.

        Does NOT return tokens — user must verify email via OTP first.

        Args:
            email: User's email address.
            password: Plain text password (8-20 chars, complexity enforced).
            username: Desired username (3-30 chars, alphanumeric + underscore).
            date_of_birth: Date string in YYYY-MM-DD format.
            ip_address: Registration IP for audit logging.

        Returns:
            Dict with user data and message (no tokens).

        Raises:
            AuthenticationError: If validation fails.
        """
        email = email.lower().strip()
        username = username.strip()

        if User.objects.filter(email=email).exists():
            raise AuthenticationError(
                "An account with this email already exists",
                code="EMAIL_EXISTS",
            )

        _validate_password(password)

        _validate_username(username)

        if User.all_objects.filter(username=username).exists():
            raise AuthenticationError(
                "This username is already taken",
                code="USERNAME_TAKEN",
            )

        encrypted_dob = _validate_and_encrypt_dob(date_of_birth)

        try:
            user = User.objects.create_user(
                email=email,
                username=username,
                password=password,
                encrypted_dob=encrypted_dob,
                last_login_ip=ip_address,
            )
        except IntegrityError:
            raise AuthenticationError(
                "This username is already taken. Please choose another.",
                code="USERNAME_TAKEN",
            ) from None

        AuthService._send_otp(user.email, str(user.id), purpose="verify")

        log_security_event(
            "auth.register.success",
            user_id=str(user.id),
            ip_address=ip_address,
            metadata={"email": mask_email(email), "username": username},
        )

        return {
            "user": user,
            "message": "Registration successful. Please verify your email with the OTP sent.",
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

        user.last_login_ip = ip_address
        user.save(update_fields=["last_login_ip", "updated_at"])

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
    def send_verification_otp(email: str) -> bool:
        """Send a 6-digit verification OTP to the user's email.

        Args:
            email: User's email address.

        Returns:
            True (always, to prevent email enumeration).
        """
        email = email.lower().strip()

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            return True

        if user.is_email_verified:
            raise AuthenticationError(
                "Email is already verified",
                code="EMAIL_ALREADY_VERIFIED",
            )

        AuthService._send_otp(email, str(user.id), purpose="verify")
        return True

    @staticmethod
    def verify_email_otp(email: str, code: str) -> dict[str, Any]:
        """Verify email using OTP code and return tokens immediately.

        Args:
            email: User's email address.
            code: 6-digit OTP code.

        Returns:
            Dict with user, access_token, refresh_token.

        Raises:
            AuthenticationError: If OTP is invalid/expired or max attempts reached.
        """
        email = email.lower().strip()

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            raise AuthenticationError(
                "Invalid email or verification code",
                code="INVALID_OTP",
            ) from None

        if user.is_email_verified:
            raise AuthenticationError(
                "Email is already verified",
                code="EMAIL_ALREADY_VERIFIED",
            )

        AuthService._check_otp_attempts(email, purpose="verify", max_attempts=5)

        try:
            from django_redis import get_redis_connection

            redis_conn = get_redis_connection("default")
            redis_key = f"otp:verify:{user.id}"
            stored_otp = redis_conn.get(redis_key)

            if stored_otp is None:
                AuthService._increment_otp_attempts(email, purpose="verify")
                raise AuthenticationError(
                    "Verification code has expired. Please request a new one.",
                    code="OTP_EXPIRED",
                )

            if stored_otp.decode() != code:
                AuthService._increment_otp_attempts(email, purpose="verify")
                raise AuthenticationError(
                    "Invalid verification code",
                    code="INVALID_OTP",
                )

            redis_conn.delete(redis_key)
            redis_conn.delete(f"otp_attempts:verify:{email}")

        except AuthenticationError:
            raise
        except Exception as e:
            logger.error(f"OTP validation failed: {e}")
            raise AuthenticationError(
                "Failed to validate code. Please try again.",
                code="OTP_VALIDATION_FAILED",
            ) from e

        user.is_email_verified = True
        user.save(update_fields=["is_email_verified", "updated_at"])

        access_token = TokenService.generate_access_token(str(user.id), user.role)
        refresh_token, _ = TokenService.generate_refresh_token(str(user.id))

        log_security_event(
            "auth.email_verified",
            user_id=str(user.id),
        )

        return {
            "user": user,
            "access_token": access_token,
            "refresh_token": refresh_token,
        }

    @staticmethod
    def resend_verification_otp(email: str) -> bool:
        """Resend verification OTP with rate limiting.

        Rate limit: 3 per 10 minutes per email.

        Args:
            email: User's email address.

        Returns:
            True (always, to prevent email enumeration).

        Raises:
            AuthenticationError: If rate limited.
        """
        email = email.lower().strip()

        AuthService._check_resend_limit(email, purpose="verify", max_resends=3)

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            return True

        if user.is_email_verified:
            raise AuthenticationError(
                "Email is already verified",
                code="EMAIL_ALREADY_VERIFIED",
            )

        AuthService._send_otp(email, str(user.id), purpose="verify")
        AuthService._increment_resend_count(email, purpose="verify")

        return True

    @staticmethod
    def suggest_usernames(email: str, date_of_birth: str) -> list[str]:
        """Generate 4 unique, available username suggestions.

        Algorithm:
        1. Extract email local part, sanitize
        2. Generate variations using birth year/month/day
        3. Check DB for uniqueness
        4. Never expose taken usernames

        Args:
            email: User's email address.
            date_of_birth: DOB string in YYYY-MM-DD format.

        Returns:
            List of 4 available username strings.

        Raises:
            AuthenticationError: If inputs are invalid.
        """
        email = email.lower().strip()

        local_part = email.split("@")[0]
        base = re.sub(r"[^a-zA-Z0-9_]", "", local_part).lower()
        base = base[:15]

        if len(base) < 2:
            base = "ziona_user"

        try:
            dob = date.fromisoformat(date_of_birth)
        except ValueError:
            raise AuthenticationError(
                "Invalid date format. Use YYYY-MM-DD.",
                code="INVALID_DATE_FORMAT",
            ) from None

        year = str(dob.year)
        year_short = year[-2:]
        month = f"{dob.month:02d}"
        day = f"{dob.day:02d}"

        candidates = [
            f"{base}{year}",
            f"{base}_{month}{day}",
            f"{base}_{year_short}",
            f"{base}{year_short}{month}",
            f"{base}_{day}{month}",
            f"{base}{year_short}{day}",
        ]

        suggestions = []
        seen = set()
        for candidate in candidates:
            candidate = candidate[:30]
            if candidate in seen:
                continue
            seen.add(candidate)

            if len(candidate) < 3:
                continue

            if not User.all_objects.filter(username=candidate).exists():
                suggestions.append(candidate)
                if len(suggestions) >= 4:
                    break

        while len(suggestions) < 4:
            suffix = "".join(random.choices(string.digits, k=3))  # noqa: S311
            candidate = f"{base}_{suffix}"[:30]
            if candidate not in seen:
                seen.add(candidate)
                if not User.all_objects.filter(username=candidate).exists():
                    suggestions.append(candidate)

        return suggestions[:4]

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
            return True

        otp = "".join(secrets.choice(string.digits) for _ in range(6))

        try:
            from django_redis import get_redis_connection

            redis_conn = get_redis_connection("default")
            redis_key = f"otp:password_reset:{user.id}"
            redis_conn.setex(redis_key, 600, otp)
        except Exception as e:
            logger.error(f"Failed to store OTP in Redis: {e}")
            raise AuthenticationError(
                "Failed to send reset code. Please try again.",
                code="OTP_STORAGE_FAILED",
            ) from e

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

            redis_conn.delete(redis_key)

        except AuthenticationError:
            raise
        except Exception as e:
            logger.error(f"OTP validation failed: {e}")
            raise AuthenticationError(
                "Failed to validate reset code. Please try again.",
                code="OTP_VALIDATION_FAILED",
            ) from e

        _validate_password(new_password)
        user.set_password(new_password)
        user.save(update_fields=["password", "updated_at"])

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
        TokenService.blacklist_access_token(access_token)

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
    def _send_otp(email: str, user_id: str, purpose: str = "verify") -> None:
        """Generate and send a 6-digit OTP via email.

        Args:
            email: Recipient email.
            user_id: User UUID string.
            purpose: OTP purpose key for Redis namespacing.
        """
        otp = "".join(secrets.choice(string.digits) for _ in range(6))

        try:
            from django_redis import get_redis_connection

            redis_conn = get_redis_connection("default")
            redis_key = f"otp:{purpose}:{user_id}"
            redis_conn.setex(redis_key, 600, otp)
        except Exception as e:
            logger.error(f"Failed to store OTP in Redis: {e}")
            raise AuthenticationError(
                "Failed to send verification code. Please try again.",
                code="OTP_STORAGE_FAILED",
            ) from e

        from core.shared.tasks.email_tasks import send_email_async

        if purpose == "verify":
            subject = "Ziona - Email Verification Code"
            message = (
                f"Welcome to Ziona!\n\n"
                f"Your email verification code is: {otp}\n\n"
                f"This code expires in 10 minutes.\n\n"
                f"- The Ziona Team"
            )
        else:
            subject = f"Ziona - {purpose.replace('_', ' ').title()} Code"
            message = f"Your code is: {otp}\n\nThis code expires in 10 minutes."

        send_email_async.delay(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
        )

    @staticmethod
    def _check_otp_attempts(email: str, purpose: str, max_attempts: int = 5) -> None:
        """Check if OTP attempt limit has been reached.

        Rate limit: max_attempts per 10 minutes per email.
        """
        try:
            from django_redis import get_redis_connection

            redis_conn = get_redis_connection("default")
            key = f"otp_attempts:{purpose}:{email}"
            attempts = redis_conn.get(key)
            if attempts and int(attempts) >= max_attempts:
                raise AuthenticationError(
                    "Too many verification attempts. Please try again later.",
                    code="OTP_RATE_LIMITED",
                )
        except AuthenticationError:
            raise
        except Exception:
            logger.debug("Failed to check OTP attempts", exc_info=True)

    @staticmethod
    def _increment_otp_attempts(email: str, purpose: str) -> None:
        """Increment the OTP attempt counter."""
        try:
            from django_redis import get_redis_connection

            redis_conn = get_redis_connection("default")
            key = f"otp_attempts:{purpose}:{email}"
            pipe = redis_conn.pipeline()
            pipe.incr(key)
            pipe.expire(key, 600)
            pipe.execute()
        except Exception:
            logger.debug("Failed to increment OTP attempts", exc_info=True)

    @staticmethod
    def _check_resend_limit(email: str, purpose: str, max_resends: int = 3) -> None:
        """Check if OTP resend limit has been reached.

        Rate limit: max_resends per 10 minutes per email.
        """
        try:
            from django_redis import get_redis_connection

            redis_conn = get_redis_connection("default")
            key = f"otp_resend:{purpose}:{email}"
            count = redis_conn.get(key)
            if count and int(count) >= max_resends:
                raise AuthenticationError(
                    "Too many resend requests. Please try again later.",
                    code="RESEND_RATE_LIMITED",
                )
        except AuthenticationError:
            raise
        except Exception:
            logger.debug("Failed to check resend limit", exc_info=True)

    @staticmethod
    def _increment_resend_count(email: str, purpose: str) -> None:
        """Increment the OTP resend counter."""
        try:
            from django_redis import get_redis_connection

            redis_conn = get_redis_connection("default")
            key = f"otp_resend:{purpose}:{email}"
            pipe = redis_conn.pipeline()
            pipe.incr(key)
            pipe.expire(key, 600)
            pipe.execute()
        except Exception:
            logger.debug("Failed to increment resend count", exc_info=True)


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


def _validate_username(username: str) -> None:
    """Validate username format.

    Rules:
    - 3-30 characters
    - Alphanumeric + underscore only
    - Cannot start with underscore
    - No consecutive underscores
    - Not a reserved word

    Raises:
        AuthenticationError: If username format is invalid.
    """
    if len(username) < 3 or len(username) > 30:
        raise AuthenticationError(
            "Username must be 3-30 characters",
            code="USERNAME_LENGTH_INVALID",
        )

    if not re.match(r"^[a-zA-Z0-9_]+$", username):
        raise AuthenticationError(
            "Username can only contain letters, numbers, and underscores",
            code="USERNAME_INVALID_CHARS",
        )

    if username.startswith("_"):
        raise AuthenticationError(
            "Username cannot start with an underscore",
            code="USERNAME_INVALID_FORMAT",
        )

    if "__" in username:
        raise AuthenticationError(
            "Username cannot contain consecutive underscores",
            code="USERNAME_INVALID_FORMAT",
        )

    reserved = {
        "admin",
        "root",
        "system",
        "ziona",
        "support",
        "help",
        "moderator",
        "mod",
        "official",
        "staff",
        "null",
        "undefined",
    }
    if username.lower() in reserved:
        raise AuthenticationError(
            "This username is reserved",
            code="USERNAME_RESERVED",
        )


def _validate_and_encrypt_dob(dob_str: str) -> bytes:
    """Validate age requirement and encrypt DOB.

    Args:
        dob_str: Date string in YYYY-MM-DD format.

    Returns:
        Fernet-encrypted DOB bytes.

    Raises:
        AuthenticationError: If validation fails.
    """
    try:
        birth_date = date.fromisoformat(dob_str)
    except ValueError:
        raise AuthenticationError(
            "Invalid date format. Use YYYY-MM-DD.",
            code="INVALID_DATE_FORMAT",
        ) from None

    today = datetime.now(timezone.utc).date()
    age = (today - birth_date).days / 365.25

    if age < 13:
        raise AuthenticationError(
            "You must be at least 13 years old to use Ziona",
            code="AGE_REQUIREMENT_NOT_MET",
        )

    if age > 120:
        raise AuthenticationError(
            "Please enter a valid date of birth",
            code="INVALID_DATE_OF_BIRTH",
        )

    try:
        cipher = Fernet(settings.ENCRYPTION_KEY.encode())
        return cipher.encrypt(dob_str.encode())
    except Exception as e:
        logger.error(f"DOB encryption failed: {e}")
        raise AuthenticationError(
            "Failed to store date of birth",
            code="ENCRYPTION_FAILED",
        ) from e


def _generate_unique_username(email: str, name: str) -> str:
    """Generate a unique username from email or name.

    Used for Google OAuth auto-registration.

    Args:
        email: User's email address.
        name: User's full name.

    Returns:
        A unique username string.
    """
    if name:
        base = re.sub(r"[^a-zA-Z0-9_]", "", name.lower().replace(" ", "_"))
    else:
        base = email.split("@")[0]
        base = re.sub(r"[^a-zA-Z0-9_]", "", base)

    base = base[:20]

    username = base
    counter = 1
    while User.all_objects.filter(username=username).exists():
        username = f"{base}_{counter}"
        counter += 1

    return username

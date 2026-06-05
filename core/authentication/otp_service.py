"""
OTP service — all OTP send/verify logic (unified + legacy).

Handles OTP generation, Redis storage, rate limiting, progressive resend
delays, and purpose-specific verification actions.
"""

import json
import logging
import secrets
import string
import uuid
from typing import Any

from django.conf import settings

from core.authentication.account_status import ensure_account_can_authenticate
from core.authentication.activity import record_successful_auth
from core.authentication.tokens import TokenService
from core.authentication.validators import AuthenticationError
from core.shared.logging import log_security_event, mask_email
from core.users.models import User

logger = logging.getLogger("core.authentication")


class OTPService:
    """Service handling all OTP operations."""

    ACCOUNT_ACTION_PURPOSES = ("account_deactivation", "account_deletion")
    VALID_OTP_PURPOSES = (
        "registration",
        "email_verification",
        "password_reset",
        *ACCOUNT_ACTION_PURPOSES,
    )

    RESEND_DELAYS = [0, 30, 60]
    MAX_RESENDS_PER_PURPOSE = 3

    @staticmethod
    def unified_send_otp(
        email: str,
        purpose: str,
        ip_address: str | None = None,
    ) -> dict[str, Any]:
        """Send a 6-digit OTP for any supported purpose.

        Validates purpose, checks user state per purpose rules,
        enforces progressive resend delays, and sends OTP via email.

        Args:
            email: User's email address.
            purpose: One of "registration", "email_verification", "password_reset".
            ip_address: Client IP for audit logging.

        Returns:
            Dict with message, expiresIn, purpose, resendAfter.

        Raises:
            AuthenticationError: If validation or rate limiting fails.
        """
        email = email.lower().strip()

        if purpose not in OTPService.VALID_OTP_PURPOSES:
            raise AuthenticationError(
                f"Invalid purpose. Must be one of: {', '.join(OTPService.VALID_OTP_PURPOSES)}",
                code="INVALID_PURPOSE",
            )

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            if purpose == "password_reset":
                return {
                    "message": "If an account exists, a code has been sent.",
                    "expires_in": 600,
                    "purpose": purpose,
                    "resend_after": 0,
                }
            raise AuthenticationError(
                "No account found with this email.",
                code="USER_NOT_FOUND",
            ) from None

        ensure_account_can_authenticate(user)

        if purpose in ("registration", "email_verification") and user.is_email_verified:
            raise AuthenticationError(
                "This email is already verified."
                if purpose == "email_verification"
                else "This email is already registered.",
                code="EMAIL_ALREADY_VERIFIED"
                if purpose == "email_verification"
                else "EMAIL_ALREADY_REGISTERED",
            )

        resend_after = 0
        try:
            from django_redis import get_redis_connection

            redis_conn = get_redis_connection("default")
            resend_key = f"otp:resend:{purpose}:{email}"
            count_raw = redis_conn.get(resend_key)
            send_count = int(count_raw) if count_raw else 0

            if send_count >= OTPService.MAX_RESENDS_PER_PURPOSE:
                raise AuthenticationError(
                    "Too many requests. Please wait before requesting another code.",
                    code="RATE_LIMIT_EXCEEDED",
                    details={"retryAfter": 600},
                )

            if send_count > 0 and send_count <= len(OTPService.RESEND_DELAYS):
                cooldown_key = f"otp:cooldown:{purpose}:{email}"
                ttl = redis_conn.ttl(cooldown_key)
                if ttl and ttl > 0:
                    raise AuthenticationError(
                        f"Please wait {ttl} seconds before resending.",
                        code="RESEND_COOLDOWN",
                        details={"resendAfter": ttl},
                    )

            next_delay = 0
            next_index = send_count + 1
            if next_index < len(OTPService.RESEND_DELAYS):
                next_delay = OTPService.RESEND_DELAYS[next_index]
            elif next_index == len(OTPService.RESEND_DELAYS):
                next_delay = OTPService.RESEND_DELAYS[-1]

            if next_delay > 0:
                cooldown_key = f"otp:cooldown:{purpose}:{email}"
                redis_conn.setex(cooldown_key, next_delay, "1")

            resend_after = next_delay

            pipe = redis_conn.pipeline()
            pipe.incr(resend_key)
            pipe.expire(resend_key, 600)
            pipe.execute()

        except AuthenticationError:
            raise
        except Exception:
            logger.debug("Failed to check resend limits", exc_info=True)

        OTPService._send_otp(email, str(user.id), purpose=purpose)

        log_security_event(
            f"auth.otp.send.{purpose}",
            user_id=str(user.id),
            ip_address=ip_address,
            metadata={"email": mask_email(email), "purpose": purpose},
        )

        return {
            "message": "Verification code sent to your email.",
            "expires_in": 600,
            "purpose": purpose,
            "resend_after": resend_after,
        }

    @staticmethod
    def unified_verify_otp(
        email: str,
        code: str,
        purpose: str,
        ip_address: str | None = None,
    ) -> dict[str, Any]:
        """Verify an OTP for any supported purpose.

        Purpose-namespaced Redis keys prevent cross-purpose reuse.

        Args:
            email: User's email address.
            code: 6-digit OTP code.
            purpose: Must match the purpose used when sending.
            ip_address: Client IP for audit logging.

        Returns:
            registration/email_verification: Dict with user, access_token, refresh_token.
            password_reset: Dict with reset_token, expires_in.
            account_deactivation/account_deletion: Dict with purpose, verified.

        Raises:
            AuthenticationError: If OTP is invalid/expired or max attempts reached.
        """
        email = email.lower().strip()
        max_attempts = 5

        if purpose not in OTPService.VALID_OTP_PURPOSES:
            raise AuthenticationError(
                f"Invalid purpose. Must be one of: {', '.join(OTPService.VALID_OTP_PURPOSES)}",
                code="INVALID_PURPOSE",
            )

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            raise AuthenticationError(
                "Invalid email or verification code.",
                code="INVALID_OTP",
            ) from None

        ensure_account_can_authenticate(user)

        if purpose in ("registration", "email_verification") and user.is_email_verified:
            raise AuthenticationError(
                "Email is already verified. Please log in.",
                code="EMAIL_ALREADY_VERIFIED",
            )

        OTPService._check_otp_attempts(email, purpose=purpose, max_attempts=max_attempts)

        try:
            from django_redis import get_redis_connection

            redis_conn = get_redis_connection("default")
            redis_key = f"otp:{purpose}:{user.id}"
            stored_otp = redis_conn.get(redis_key)

            attempts_key = f"otp_attempts:{purpose}:{email}"
            current_attempts = redis_conn.get(attempts_key)
            used = int(current_attempts) if current_attempts else 0

            if stored_otp is None:
                OTPService._increment_otp_attempts(email, purpose=purpose)
                raise AuthenticationError(
                    "Verification code has expired. Please request a new one.",
                    code="OTP_EXPIRED",
                )

            if stored_otp.decode() != code:
                OTPService._increment_otp_attempts(email, purpose=purpose)
                remaining = max(0, max_attempts - used - 1)
                raise AuthenticationError(
                    "Invalid verification code. Please try again.",
                    code="INVALID_OTP",
                    details={"attemptsRemaining": remaining},
                )

            redis_conn.delete(redis_key)
            redis_conn.delete(attempts_key)
            redis_conn.delete(f"otp:resend:{purpose}:{email}")
            redis_conn.delete(f"otp:cooldown:{purpose}:{email}")

        except AuthenticationError:
            raise
        except Exception as e:
            logger.error("OTP validation failed: %s", e, exc_info=True)
            raise AuthenticationError(
                "Failed to validate code. Please try again.",
                code="OTP_VALIDATION_FAILED",
            ) from e

        if purpose in ("registration", "email_verification"):
            user.is_email_verified = True
            user.save(update_fields=["is_email_verified", "updated_at"])
            record_successful_auth(user, ip_address)

            access_token = TokenService.generate_access_token(str(user.id), user.role)
            refresh_token, _ = TokenService.generate_refresh_token(str(user.id))

            log_security_event(
                f"auth.otp.verified.{purpose}",
                user_id=str(user.id),
                ip_address=ip_address,
            )
            from core.emails.services import EmailService

            EmailService.send_welcome_email(user.username or user.full_name, user.email)

            return {
                "user": user,
                "access_token": access_token,
                "refresh_token": refresh_token,
                "purpose": purpose,
            }

        if purpose == "password_reset":
            reset_token = str(uuid.uuid4())

            try:
                from django_redis import get_redis_connection

                redis_conn = get_redis_connection("default")
                redis_conn.setex(
                    f"reset_token:{reset_token}",
                    900,
                    json.dumps({"email": email, "user_id": str(user.id)}),
                )
            except Exception as e:
                logger.error("Failed to store reset token: %s", e)
                raise AuthenticationError(
                    "Failed to process request. Please try again.",
                    code="TOKEN_STORAGE_FAILED",
                ) from e

            log_security_event(
                "auth.otp.verified.password_reset",
                user_id=str(user.id),
                ip_address=ip_address,
            )

            return {
                "reset_token": reset_token,
                "expires_in": 900,
                "purpose": purpose,
            }

        if purpose in OTPService.ACCOUNT_ACTION_PURPOSES:
            log_security_event(
                f"auth.otp.verified.{purpose}",
                user_id=str(user.id),
                ip_address=ip_address,
            )
            return {
                "purpose": purpose,
                "verified": True,
            }

        raise AuthenticationError("Unexpected purpose.", code="INVALID_PURPOSE")

    @staticmethod
    def verify_account_action_otp(
        email: str,
        user_id: str,
        code: str,
        purpose: str,
    ) -> bool:
        """Verify an account-action OTP without changing account state."""
        email = email.lower().strip()
        code = code.strip()
        max_attempts = 5

        if purpose not in OTPService.ACCOUNT_ACTION_PURPOSES:
            raise AuthenticationError(
                "Invalid account action OTP purpose.",
                code="INVALID_PURPOSE",
            )

        OTPService._check_otp_attempts(email, purpose=purpose, max_attempts=max_attempts)

        try:
            from django_redis import get_redis_connection

            redis_conn = get_redis_connection("default")
            redis_key = f"otp:{purpose}:{user_id}"
            stored_otp = redis_conn.get(redis_key)

            attempts_key = f"otp_attempts:{purpose}:{email}"
            current_attempts = redis_conn.get(attempts_key)
            used = int(current_attempts) if current_attempts else 0

            if stored_otp is None:
                OTPService._increment_otp_attempts(email, purpose=purpose)
                raise AuthenticationError(
                    "Verification code has expired. Please request a new one.",
                    code="OTP_EXPIRED",
                )

            if stored_otp.decode() != code:
                OTPService._increment_otp_attempts(email, purpose=purpose)
                remaining = max(0, max_attempts - used - 1)
                raise AuthenticationError(
                    "Invalid verification code. Please try again.",
                    code="INVALID_OTP",
                    details={"attemptsRemaining": remaining},
                )

            redis_conn.delete(redis_key)
            redis_conn.delete(attempts_key)
            redis_conn.delete(f"otp:resend:{purpose}:{email}")
            redis_conn.delete(f"otp:cooldown:{purpose}:{email}")
        except AuthenticationError:
            raise
        except Exception as e:
            logger.error("Account action OTP validation failed: %s", e, exc_info=True)
            raise AuthenticationError(
                "Failed to validate code. Please try again.",
                code="OTP_VALIDATION_FAILED",
            ) from e

        return True

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

        ensure_account_can_authenticate(user)

        if user.is_email_verified:
            raise AuthenticationError(
                "Email is already verified",
                code="EMAIL_ALREADY_VERIFIED",
            )

        OTPService._send_otp(email, str(user.id), purpose="verify")
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
        max_attempts = 5

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            logger.warning("OTP verification for non-existent email=%s", mask_email(email))
            raise AuthenticationError(
                "Invalid email or verification code.",
                code="INVALID_OTP",
            ) from None

        ensure_account_can_authenticate(user)

        if user.is_email_verified:
            raise AuthenticationError(
                "Email is already verified. Please log in.",
                code="EMAIL_ALREADY_VERIFIED",
            )

        OTPService._check_otp_attempts(email, purpose="verify", max_attempts=max_attempts)

        try:
            from django_redis import get_redis_connection

            redis_conn = get_redis_connection("default")
            redis_key = f"otp:verify:{user.id}"
            stored_otp = redis_conn.get(redis_key)

            attempts_key = f"otp_attempts:verify:{email}"
            current_attempts = redis_conn.get(attempts_key)
            used = int(current_attempts) if current_attempts else 0

            if stored_otp is None:
                OTPService._increment_otp_attempts(email, purpose="verify")
                logger.info("OTP expired for user_id=%s", user.id)
                raise AuthenticationError(
                    "Verification code has expired. Please request a new one.",
                    code="OTP_EXPIRED",
                )

            if stored_otp.decode() != code:
                OTPService._increment_otp_attempts(email, purpose="verify")
                remaining = max(0, max_attempts - used - 1)
                logger.warning(
                    "Invalid OTP for user_id=%s attempts_remaining=%d",
                    user.id,
                    remaining,
                )
                raise AuthenticationError(
                    "Invalid verification code. Please try again.",
                    code="INVALID_OTP",
                    details={"attemptsRemaining": remaining},
                )

            redis_conn.delete(redis_key)
            redis_conn.delete(attempts_key)

        except AuthenticationError:
            raise
        except Exception as e:
            logger.error("OTP validation failed: %s", e, exc_info=True)
            raise AuthenticationError(
                "Failed to validate code. Please try again.",
                code="OTP_VALIDATION_FAILED",
            ) from e

        user.is_email_verified = True
        user.save(update_fields=["is_email_verified", "updated_at"])
        record_successful_auth(user)

        from django.core.cache import cache

        cache.delete(f"user_me_data_{user.id}")

        access_token = TokenService.generate_access_token(str(user.id), user.role)
        refresh_token, _ = TokenService.generate_refresh_token(str(user.id))

        logger.info("Email verified: user_id=%s", user.id)
        log_security_event(
            "auth.email_verified",
            user_id=str(user.id),
        )
        from core.emails.services import EmailService

        EmailService.send_welcome_email(user.username or user.full_name, user.email)

        return {
            "user": user,
            "access_token": access_token,
            "refresh_token": refresh_token,
        }

    @staticmethod
    def resend_verification_otp(email: str) -> dict[str, Any]:
        """Resend verification OTP with rate limiting.

        Rate limit: 3 per 10 minutes per email.

        Args:
            email: User's email address.

        Returns:
            Dict with message and expiresIn seconds.

        Raises:
            AuthenticationError: If rate limited or already verified.
        """
        email = email.lower().strip()

        OTPService._check_resend_limit(email, purpose="verify", max_resends=3)

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            raise AuthenticationError(
                "No account found with this email address.",
                code="USER_NOT_FOUND",
            ) from None

        ensure_account_can_authenticate(user)

        if user.is_email_verified:
            raise AuthenticationError(
                "Email is already verified. Please log in.",
                code="EMAIL_ALREADY_VERIFIED",
            )

        OTPService._send_otp(email, str(user.id), purpose="verify")
        OTPService._increment_resend_count(email, purpose="verify")

        logger.info("OTP resent for user_id=%s", user.id)

        return {
            "message": "Verification code sent to your email.",
            "expires_in": 600,
        }

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

        user_name = "Friend"
        try:
            user = User.all_objects.get(id=user_id)
            user_name = user.username or user.full_name or "Friend"
        except User.DoesNotExist:
            logger.debug("Unable to resolve user name for OTP email", exc_info=True)

        if purpose in ("verify", "registration", "email_verification"):
            from core.emails.services import EmailService

            if not EmailService.send_verify_email(user_name, email, otp):
                raise AuthenticationError(
                    "We could not send your verification code. Please try again.",
                    code="OTP_EMAIL_QUEUE_FAILED",
                )
            return

        if purpose == "password_reset":
            from core.emails.services import EmailService

            if not EmailService.send_reset_password(user_name, email, otp):
                raise AuthenticationError(
                    "We could not send your reset code. Please try again.",
                    code="OTP_EMAIL_QUEUE_FAILED",
                )
            return

        from core.shared.tasks.email_tasks import send_email_async

        send_email_async.delay(
            subject=f"Ziona - {purpose.replace('_', ' ').title()} Code",
            message=f"Your code is: {otp}\n\nThis code expires in 10 minutes.",
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
                logger.warning(
                    "OTP max attempts reached for email=%s purpose=%s",
                    mask_email(email),
                    purpose,
                )
                raise AuthenticationError(
                    "Too many failed attempts. Please request a new verification code.",
                    code="MAX_ATTEMPTS_REACHED",
                    details={"retryAfter": 600},
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
                logger.warning(
                    "OTP resend rate limited for email=%s purpose=%s",
                    mask_email(email),
                    purpose,
                )
                raise AuthenticationError(
                    "Too many requests. Please wait before requesting another code.",
                    code="RATE_LIMIT_EXCEEDED",
                    details={"retryAfter": 600},
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

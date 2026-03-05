"""
Password service — password reset flows (legacy + resetToken).

Handles OTP-based and token-based password reset operations.
"""

import json
import logging
import secrets
import string
from typing import Any

from django.conf import settings

from core.authentication.tokens import TokenService
from core.authentication.validators import AuthenticationError, validate_password
from core.shared.logging import log_security_event, mask_email
from core.users.models import User

logger = logging.getLogger("core.authentication")


class PasswordService:
    """Service handling password reset operations."""

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
        """Reset user's password using OTP (legacy flow).

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

        validate_password(new_password)
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
    def reset_password_with_token(
        reset_token: str,
        new_password: str,
        sign_out_all_devices: bool = False,
        ip_address: str | None = None,
    ) -> dict[str, Any]:
        """Reset password using a short-lived reset token (step 2 of unified flow).

        Args:
            reset_token: UUID token from OTPService.unified_verify_otp(purpose="password_reset").
            new_password: New password (complexity enforced).
            sign_out_all_devices: If True, revoke ALL sessions including current.
            ip_address: Client IP for audit logging.

        Returns:
            Dict with user, access_token, refresh_token.

        Raises:
            AuthenticationError: If token is invalid/expired or password fails validation.
        """
        # 1. Validate reset token from Redis
        try:
            from django_redis import get_redis_connection

            redis_conn = get_redis_connection("default")
            token_key = f"reset_token:{reset_token}"
            stored_data = redis_conn.get(token_key)

            if stored_data is None:
                raise AuthenticationError(
                    "Reset token has expired or is invalid. Please start over.",
                    code="INVALID_RESET_TOKEN",
                )

            data = json.loads(stored_data.decode())
            email = data["email"]
            user_id = data["user_id"]

            # Consume the token (one-time use)
            redis_conn.delete(token_key)

        except AuthenticationError:
            raise
        except Exception as e:
            logger.error("Reset token validation failed: %s", e)
            raise AuthenticationError(
                "Failed to validate reset token.",
                code="TOKEN_VALIDATION_FAILED",
            ) from e

        # 2. Look up user
        try:
            user = User.objects.get(id=user_id, email=email)
        except User.DoesNotExist:
            raise AuthenticationError(
                "User not found.",
                code="USER_NOT_FOUND",
            ) from None

        # 3. Validate and set new password
        validate_password(new_password)
        user.set_password(new_password)
        user.save(update_fields=["password", "updated_at"])

        # 4. Revoke tokens
        TokenService.revoke_all_user_tokens(str(user.id))

        # 5. Issue new tokens for the current session
        access_token = TokenService.generate_access_token(str(user.id), user.role)
        refresh_token, _ = TokenService.generate_refresh_token(str(user.id))

        log_security_event(
            "auth.password_reset.success",
            user_id=str(user.id),
            ip_address=ip_address,
        )

        return {
            "user": user,
            "access_token": access_token,
            "refresh_token": refresh_token,
        }

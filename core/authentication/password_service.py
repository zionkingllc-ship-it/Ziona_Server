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

            redis_conn.delete(token_key)

        except AuthenticationError:
            raise
        except Exception as e:
            logger.error("Reset token validation failed: %s", e)
            raise AuthenticationError(
                "Failed to validate reset token.",
                code="TOKEN_VALIDATION_FAILED",
            ) from e

        try:
            user = User.objects.get(id=user_id, email=email)
        except User.DoesNotExist:
            raise AuthenticationError(
                "User not found.",
                code="USER_NOT_FOUND",
            ) from None

        validate_password(new_password)
        user.set_password(new_password)
        user.save(update_fields=["password", "updated_at"])

        TokenService.revoke_all_user_tokens(str(user.id))

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

    @staticmethod
    def add_password(user_id: str, password: str) -> dict[str, Any]:
        """Add a password for an OAuth user who doesn't have one.

        Enables email+password login alongside OAuth.

        Args:
            user_id: UUID of the authenticated user.
            password: New password (8-20 chars, 1 letter, 1 number, 1 special).

        Returns:
            Dict with success=True and user.

        Raises:
            AuthenticationError: If user already has a password or
                password is too weak.
        """
        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            raise AuthenticationError(
                "User not found.",
                code="USER_NOT_FOUND",
            ) from None

        if user.has_usable_password():
            raise AuthenticationError(
                "You already have a password. Use 'Change Password' instead.",
                code="PASSWORD_ALREADY_EXISTS",
            )

        validate_password(password)
        user.set_password(password)
        user.save(update_fields=["password", "updated_at"])

        from django.core.cache import cache

        cache.delete(f"user_me_data_{user.id}")

        log_security_event(
            "auth.password_added",
            user_id=str(user.id),
            metadata={"auth_provider": user.auth_provider},
        )

        return {"user": user}

    @staticmethod
    def change_password(
        user_id: str,
        current_password: str,
        new_password: str,
        sign_out_other_devices: bool = False,
        current_jti: str | None = None,
        ip_address: str | None = None,
    ) -> dict[str, Any]:
        """Change password for an authenticated user.

        Optionally sign out all other devices by revoking their tokens.

        Args:
            user_id: UUID of the authenticated user.
            current_password: Current password for verification.
            new_password: New password (8-20 chars, complexity enforced).
            sign_out_other_devices: If True, invalidate all other sessions.
            current_jti: JTI of the current refresh token to keep alive.
            ip_address: Client IP for audit logging.

        Returns:
            Dict with message and signed_out_devices count.

        Raises:
            AuthenticationError: If current password is wrong or new
                password doesn't meet requirements.
        """
        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            raise AuthenticationError(
                "User not found.",
                code="USER_NOT_FOUND",
            ) from None

        if not user.check_password(current_password):
            raise AuthenticationError(
                "Current password is incorrect.",
                code="CURRENT_PASSWORD_INCORRECT",
            )

        validate_password(new_password)
        user.set_password(new_password)
        user.save(update_fields=["password", "updated_at"])

        signed_out_devices = 0

        if sign_out_other_devices and current_jti:
            signed_out_devices = TokenService.revoke_all_user_tokens_except(
                user_id=str(user.id),
                keep_jti=current_jti,
            )
            event_name = "auth.password_changed_with_device_signout"
        else:
            event_name = "auth.password_changed"

        log_security_event(
            event_name,
            user_id=str(user.id),
            ip_address=ip_address,
            metadata={"signed_out_devices": signed_out_devices},
        )

        return {
            "message": "Password changed successfully.",
            "signed_out_devices": signed_out_devices,
        }

"""
Authentication services for Ziona Server.

Core auth operations: register, login, token refresh, logout, account deletion,
and username suggestions. OTP, password reset, and OAuth logic have been
extracted into dedicated service modules.

Follows the Service Layer Pattern: View -> Service -> ORM
"""

import logging
import re
import secrets
import string
from datetime import date
from typing import Any

from django.db import IntegrityError

from core.authentication.otp_service import OTPService
from core.authentication.tokens import TokenError, TokenService
from core.authentication.validators import (
    AuthenticationError,
    validate_and_encrypt_dob,
    validate_password,
    validate_username,
)
from core.shared.logging import log_security_event, mask_email
from core.users.models import User

logger = logging.getLogger("core.authentication")


class AuthService:
    """Service handling core authentication business logic.

    Methods:
        register: Create a new user with email/password/username/DOB
        login: Authenticate user and return JWT tokens
        suggest_usernames: Generate unique username suggestions
        delete_account: Permanently delete user account
        refresh_tokens: Rotate refresh token for new token pair
        logout: Revoke user tokens

    Delegated (backward-compatible wrappers):
        send_verification_otp -> OTPService
        verify_email_otp -> OTPService
        resend_verification_otp -> OTPService
        unified_send_otp -> OTPService
        unified_verify_otp -> OTPService
        request_password_reset -> PasswordService
        reset_password -> PasswordService
        reset_password_with_token -> PasswordService
        google_oauth_login -> OAuthService
    """

    @staticmethod
    def register(
        email: str,
        password: str,
        username: str,
        date_of_birth: str,
        ip_address: str | None = None,
    ) -> dict[str, Any]:
        """Register a new user or update an unverified user data.

        Three scenarios:
        A) New email -> create user, send OTP, return user (no tokens).
        B) Email exists + NOT verified -> update user data, send new OTP.
        C) Email exists + verified -> raise EMAIL_ALREADY_REGISTERED.
        """
        email = email.lower().strip()
        username = username.strip()

        validate_password(password)
        validate_username(username)
        encrypted_dob = validate_and_encrypt_dob(date_of_birth)

        existing_user = User.objects.filter(email=email).first()

        if existing_user and existing_user.is_email_verified:
            logger.info("Registration attempt for verified email: %s", mask_email(email))
            raise AuthenticationError(
                "An account with this email already exists. Please log in.",
                code="EMAIL_ALREADY_REGISTERED",
            )

        if existing_user and not existing_user.is_email_verified:
            username_conflict = (
                User.all_objects.filter(username=username).exclude(id=existing_user.id).exists()
            )
            if username_conflict:
                raise AuthenticationError(
                    "This username is already taken",
                    code="USERNAME_TAKEN",
                )

            existing_user.username = username
            existing_user.set_password(password)
            existing_user.encrypted_dob = encrypted_dob
            existing_user.last_login_ip = ip_address
            existing_user.save(
                update_fields=[
                    "username",
                    "password",
                    "encrypted_dob",
                    "last_login_ip",
                    "updated_at",
                ]
            )
            user = existing_user

            logger.info(
                "Updated unverified user data: user_id=%s email=%s",
                user.id,
                mask_email(email),
            )
            log_security_event(
                "auth.register.updated_unverified",
                user_id=str(user.id),
                ip_address=ip_address,
                metadata={
                    "email": mask_email(email),
                    "username": username,
                },
            )

            message = "Registration details updated. " "Check your email for verification code."
        else:
            if User.all_objects.filter(username=username).exists():
                raise AuthenticationError(
                    "This username is already taken",
                    code="USERNAME_TAKEN",
                )

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

            logger.info(
                "New user registered: user_id=%s email=%s",
                user.id,
                mask_email(email),
            )
            log_security_event(
                "auth.register.success",
                user_id=str(user.id),
                ip_address=ip_address,
                metadata={"email": mask_email(email), "username": username},
            )

            message = "Registration successful. " "Check your email for verification code."

        OTPService._send_otp(user.email, str(user.id), purpose="verify")

        return {
            "user": user,
            "message": message,
            "requires_verification": True,
        }

    @staticmethod
    def login(
        email: str,
        password: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict[str, Any]:
        """Authenticate user with email and password.

        Scenarios:
        A) Valid credentials + verified -> return tokens.
        B) Valid credentials + NOT verified -> send OTP, requiresVerification.
        C) Invalid credentials -> raise error.
        D) Deactivated account -> raise error.
        """
        email = email.lower().strip()

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            logger.warning("Login failed: unknown email=%s", mask_email(email))
            raise AuthenticationError(
                "Invalid email or password",
                code="INVALID_CREDENTIALS",
            ) from None

        if not user.check_password(password):
            logger.warning("Login failed: bad password for user_id=%s", user.id)
            raise AuthenticationError(
                "Invalid email or password",
                code="INVALID_CREDENTIALS",
            )

        if not user.is_active:
            logger.warning("Login failed: deactivated account user_id=%s", user.id)
            raise AuthenticationError(
                "This account has been deactivated",
                code="ACCOUNT_DEACTIVATED",
            )

        if not user.is_email_verified:
            logger.info(
                "Login attempt for unverified user_id=%s -- sending OTP",
                user.id,
            )
            OTPService._send_otp(user.email, str(user.id), purpose="verify")
            log_security_event(
                "auth.login.unverified_otp_sent",
                user_id=str(user.id),
                ip_address=ip_address,
            )
            return {
                "user": user,
                "message": "Email not verified. Verification code sent to your email.",
                "requires_verification": True,
            }

        user.last_login_ip = ip_address
        user.save(update_fields=["last_login_ip", "updated_at"])

        access_token = TokenService.generate_access_token(str(user.id), user.role)
        refresh_token, _ = TokenService.generate_refresh_token(str(user.id))

        logger.info("Login success: user_id=%s ip=%s", user.id, ip_address)
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
    def delete_account(user_id: str) -> bool:
        """Permanently delete a user account and all associated data."""
        try:
            user = User.objects.get(id=user_id)
            email = user.email
            user.delete()
            logger.info("Account permanently deleted: user_id=%s email=%s", user_id, email)
            return True
        except User.DoesNotExist as e:
            logger.error("Delete account failed: user not found id=%s", user_id)
            raise AuthenticationError("User not found", "USER_NOT_FOUND") from e

    @staticmethod
    def suggest_usernames(email: str, date_of_birth: str) -> list[str]:
        """Generate 4 unique, available username suggestions."""
        email = email.lower().strip()

        local_part = email.split("@")[0]
        base = re.sub(r"[^a-zA-Z0-9_]", "", local_part).lower()

        base_short = base[:5] if len(base) >= 5 else base
        if len(base_short) < 2:
            base_short = "user"

        try:
            dob = date.fromisoformat(date_of_birth)
        except ValueError:
            raise AuthenticationError(
                "Invalid date format. Use YYYY-MM-DD.",
                code="INVALID_DATE_FORMAT",
            ) from None

        year_short = str(dob.year)[-2:]
        month = f"{dob.month:02d}"
        day = f"{dob.day:02d}"

        candidates = [
            f"{base_short}{year_short}",
            f"{base_short}{month}{day}",
            f"{base_short}{year_short}{month}",
            f"{base_short}{day}",
            f"{base_short}_{year_short}{day}",
            f"{base_short}{year_short[0]}{month}",
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
            suffix = "".join(secrets.choice(string.digits) for _ in range(2))
            candidate = f"{base_short}{suffix}"[:30]
            if candidate not in seen:
                seen.add(candidate)
                if not User.all_objects.filter(username=candidate).exists():
                    suggestions.append(candidate)

        return suggestions[:4]

    @staticmethod
    def refresh_tokens(refresh_token: str) -> dict[str, str]:
        """Rotate a refresh token for a new access/refresh token pair."""
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
        """Log out a user by revoking their tokens."""
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

    send_verification_otp = staticmethod(OTPService.send_verification_otp)
    verify_email_otp = staticmethod(OTPService.verify_email_otp)
    resend_verification_otp = staticmethod(OTPService.resend_verification_otp)
    unified_send_otp = staticmethod(OTPService.unified_send_otp)
    unified_verify_otp = staticmethod(OTPService.unified_verify_otp)
    _send_otp = staticmethod(OTPService._send_otp)
    _check_otp_attempts = staticmethod(OTPService._check_otp_attempts)
    _increment_otp_attempts = staticmethod(OTPService._increment_otp_attempts)
    _check_resend_limit = staticmethod(OTPService._check_resend_limit)
    _increment_resend_count = staticmethod(OTPService._increment_resend_count)

    @staticmethod
    def request_password_reset(email, ip_address=None):
        from core.authentication.password_service import PasswordService

        return PasswordService.request_password_reset(email, ip_address)

    @staticmethod
    def reset_password(email, otp, new_password, ip_address=None):
        from core.authentication.password_service import PasswordService

        return PasswordService.reset_password(email, otp, new_password, ip_address)

    @staticmethod
    def reset_password_with_token(
        reset_token, new_password, sign_out_all_devices=False, ip_address=None
    ):
        from core.authentication.password_service import PasswordService

        return PasswordService.reset_password_with_token(
            reset_token, new_password, sign_out_all_devices, ip_address
        )

    @staticmethod
    def google_oauth_login(id_token, ip_address=None):
        from core.authentication.oauth_service import OAuthService

        return OAuthService.google_oauth_login(id_token, ip_address)

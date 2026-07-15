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

from django.core.cache import cache
from django.db import IntegrityError

from core.authentication.account_status import (
    build_account_recovery_result,
    ensure_account_can_authenticate,
)
from core.authentication.activity import record_successful_auth
from core.authentication.otp_service import OTPService
from core.authentication.tokens import TokenError, TokenInfrastructureError, TokenService
from core.authentication.validators import (
    AuthenticationError,
    validate_and_encrypt_dob,
    validate_password,
    validate_username,
)
from core.shared.logging import log_security_event, mask_email
from core.users.models import User

logger = logging.getLogger("core.authentication")

from core.authentication import account_actions  # noqa: E402


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
    def get_me(user_id: str) -> dict[str, Any]:
        """Get authenticated user data with profile and stats.

        Returns data structured for CurrentUserResponse schema / GET /api/auth/me.
        Uses 5-minute caching mechanism invalidated on profile updates.
        """
        cache_key = f"user_me_data_{user_id}"
        cached_data = cache.get(cache_key)

        if cached_data and "accountDetails" in cached_data:
            return cached_data

        user = User.objects.filter(id=user_id, deleted_at__isnull=True).first()
        if not user:
            raise AuthenticationError("User not found", "USER_NOT_FOUND")

        ensure_account_can_authenticate(user)

        from core.profiles.services import ProfileService

        profile_dto = ProfileService.get_user_profile(user_id, viewer_id=user_id)

        response_data = {
            "id": str(user.id),
            "username": user.username or "",
            "email": user.email,
            "displayName": user.full_name or "",
            "isEmailVerified": user.is_email_verified,
            "hasPassword": user.has_usable_password(),
            # Expose the privacy setting so the mobile app can hide like counts
            # on the current user's own posts without a separate API call.
            "hideLikeCount": user.hide_like_count,
            "isEarlySupporter": user.supporter_identity.is_early_supporter
            if hasattr(user, "supporter_identity")
            else False,
            "profile": {
                "bio": profile_dto.bio,
                "bioLink": profile_dto.bio_link,
                "avatarUrl": profile_dto.avatar_url,
                "location": profile_dto.location,
            },
            "stats": {
                "postsCount": profile_dto.stats.posts_count,
                "followersCount": profile_dto.stats.followers_count,
                "followingCount": profile_dto.stats.following_count,
            },
            "lastNameChange": user.last_name_change.isoformat() if user.last_name_change else None,
            "lastUsernameChange": user.last_username_change.isoformat()
            if user.last_username_change
            else None,
            "createdAt": user.created_at.isoformat(),
            "accountDetails": {
                "memberSince": user.created_at.strftime("%B %Y"),
                "memberSinceDate": user.created_at.isoformat(),
                "location": user.location or "",
                "accountStatus": user.status,
            },
        }

        cache.set(cache_key, response_data, 300)

        return response_data

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

        existing_user = User.all_objects.filter(email=email).first()
        if existing_user:
            ensure_account_can_authenticate(existing_user)

        validate_password(password)
        validate_username(username)
        encrypted_dob = validate_and_encrypt_dob(date_of_birth)

        if existing_user and existing_user.is_email_verified:
            logger.info("Registration attempt for verified email: %s", mask_email(email))
            raise AuthenticationError(
                "An account with this email already exists. Please log in.",
                code="EMAIL_ALREADY_REGISTERED",
            )

        if existing_user and not existing_user.is_email_verified:
            logger.info(
                "Deleting unverified account to allow re-registration for email: %s",
                mask_email(email),
            )
            existing_user.delete()

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

        message = "Registration successful. Check your email for verification code."

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
            user = User.all_objects.get(email=email)
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

        recovery_result = build_account_recovery_result(user)
        if recovery_result:
            log_security_event(
                "auth.login.recovery_required",
                user_id=str(user.id),
                ip_address=ip_address,
                metadata={"reason": recovery_result["recovery_reason"]},
            )
            return recovery_result

        ensure_account_can_authenticate(user)

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

        record_successful_auth(user, ip_address)

        access_token = TokenService.generate_access_token(str(user.id), user.role)
        try:
            refresh_token, _ = TokenService.generate_refresh_token(str(user.id))
        except TokenError as e:
            logger.error("Failed to issue login refresh token: %s", e, exc_info=True)
            raise AuthenticationError(
                "Authentication service is temporarily unavailable. Please try again.",
                code="AUTH_SERVICE_UNAVAILABLE",
            ) from e

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
    def suggest_usernames(email: str, date_of_birth: str | None = None) -> list[str]:
        """Generate 4 unique, available username suggestions."""
        email = email.lower().strip()

        local_part = email.split("@")[0]
        base = re.sub(r"[^a-zA-Z0-9_]", "", local_part).lower()

        base_short = base[:5] if len(base) >= 5 else base
        if len(base_short) < 2:
            base_short = "user"

        candidates = []
        if date_of_birth:
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
    def refresh_tokens(
        refresh_token: str,
        ip_address: str | None = None,
    ) -> dict[str, str]:
        """Rotate a refresh token for a new access/refresh token pair."""
        try:
            payload = TokenService.validate_refresh_token(refresh_token)
            user_id = payload["user_id"]
            try:
                user = User.all_objects.get(id=user_id)
                ensure_account_can_authenticate(user)
                role = user.role
            except User.DoesNotExist:
                raise TokenError("User not found") from None
            try:
                result = TokenService.rotate_refresh_token(refresh_token, role)
                record_successful_auth(user, ip_address)
                logger.info(
                    "token_refresh_outcome",
                    extra={"outcome": "success", "user_id": str(user.id)},
                )
                return result
            except TokenInfrastructureError as e:
                raise AuthenticationError(str(e), code="AUTH_SERVICE_UNAVAILABLE") from e
            except TokenError as e:
                raise AuthenticationError(str(e), code="INVALID_REFRESH_TOKEN") from e
        except AuthenticationError as exc:
            logger.warning(
                "token_refresh_outcome",
                extra={"outcome": exc.code},
            )
            raise
        except TokenInfrastructureError as e:
            logger.warning(
                "token_refresh_outcome",
                extra={"outcome": "AUTH_SERVICE_UNAVAILABLE"},
            )
            raise AuthenticationError(str(e), code="AUTH_SERVICE_UNAVAILABLE") from e
        except TokenError as e:
            logger.warning(
                "token_refresh_outcome",
                extra={"outcome": "INVALID_REFRESH_TOKEN"},
            )
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

    @staticmethod
    def finalize_username(user_id: str, username: str) -> User:
        """Set the permanent username for an authenticated OAuth-style account."""
        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            raise AuthenticationError("User not found", "USER_NOT_FOUND") from None

        ensure_account_can_authenticate(user)

        username = username.strip()
        validate_username(username)

        if User.all_objects.filter(username=username).exclude(id=user.id).exists():
            raise AuthenticationError(
                "Username already exists",
                code="USERNAME_TAKEN",
            )

        user.username = username
        user.needs_username_selection = False
        user.save(update_fields=["username", "needs_username_selection", "updated_at"])
        cache.delete(f"user_me_data_{user.id}")

        return user

    @staticmethod
    def add_password(user_id: str, password: str) -> dict[str, Any]:
        """Add a password to an authenticated OAuth-style account."""
        from core.authentication.password_service import PasswordService

        return PasswordService.add_password(user_id=user_id, password=password)

    @staticmethod
    def change_password(
        user_id: str,
        current_password: str,
        new_password: str,
        sign_out_other_devices: bool = False,
        current_jti: str | None = None,
        ip_address: str | None = None,
    ) -> dict[str, Any]:
        """Change a user's password through the shared authentication service."""
        from core.authentication.password_service import PasswordService

        return PasswordService.change_password(
            user_id=user_id,
            current_password=current_password,
            new_password=new_password,
            sign_out_other_devices=sign_out_other_devices,
            current_jti=current_jti,
            ip_address=ip_address,
        )

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

    @staticmethod
    def apple_oauth_login(
        identity_token,
        nonce=None,
        raw_nonce=None,
        apple_user=None,
        ip_address=None,
    ):
        from core.authentication.oauth_service import OAuthService

        return OAuthService.apple_oauth_login(
            identity_token=identity_token,
            nonce=nonce,
            raw_nonce=raw_nonce,
            apple_user=apple_user,
            ip_address=ip_address,
        )

    # Account-lifecycle implementations live in account_actions.py;
    # the AuthService surface is unchanged.
    _verify_account_action_reauthentication = staticmethod(
        account_actions._verify_account_action_reauthentication
    )
    deactivate_account = staticmethod(account_actions.deactivate_account)
    delete_account = staticmethod(account_actions.delete_account)
    reactivate_account = staticmethod(account_actions.reactivate_account)
    cancel_account_deletion = staticmethod(account_actions.cancel_account_deletion)

import logging
from datetime import date, datetime, timezone

from cryptography.fernet import Fernet
from django.conf import settings

from core.shared.logging import log_security_event
from core.users.models import User
from core.users.validators import (
    UsernameValidationError,
    validate_username_format,
    validate_username_not_reserved,
)

logger = logging.getLogger("core.users")


class UserServiceError(Exception):
    """Raised when user service operations fail."""

    def __init__(self, message: str, code: str = "USER_ERROR"):
        self.message = message
        self.code = code
        super().__init__(message)


class UserService:
    """Service handling user profile business logic.

    Methods:
        set_username: Set or update the user's username
        set_date_of_birth: Encrypt and store date of birth
        get_date_of_birth: Decrypt and return date of birth
    """

    @staticmethod
    def set_username(user_id: str, username: str) -> User:
        """Set or update a user's username.

        Validates format, checks availability, and updates the user record.
        Called during onboarding after registration.

        Args:
            user_id: UUID of the user.
            username: Desired username (3-30 chars).

        Returns:
            Updated User instance.

        Raises:
            UserServiceError: If username is invalid or unavailable.
        """
        username = username.strip()

        try:
            validate_username_format(username)
            validate_username_not_reserved(username)
        except UsernameValidationError as e:
            raise UserServiceError(e.message, code=e.code) from None
        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            raise UserServiceError("User not found", code="USER_NOT_FOUND") from None

        if user.username == username:
            return user

        # 30-day limit check
        if user.last_username_change:
            from datetime import timedelta

            from django.utils import timezone

            days_since_change = (timezone.now() - user.last_username_change).days
            if days_since_change < 30:
                next_change = user.last_username_change + timedelta(days=30)
                raise UserServiceError(
                    f"You're allowed one username change every 30 days. Next change on {next_change.strftime('%B %d, %Y')}.",
                    code="RATE_LIMIT_EXCEEDED",
                )

        existing = User.all_objects.filter(username=username).exclude(id=user_id)
        if existing.exists():
            raise UserServiceError(
                "This username is already taken",
                code="USERNAME_TAKEN",
            )

        from django.utils import timezone

        user.username = username
        user.last_username_change = timezone.now()
        user.save(update_fields=["username", "last_username_change", "updated_at"])

        # Invalidate me-data cache so the new username is immediately visible
        # in the authenticated user's me query instead of being stale for 5 min.
        try:
            from django.core.cache import cache

            cache.delete(f"user_me_data_{user_id}")
        except Exception:
            logger.warning("Failed to clear user_me_data cache after set_username")

        log_security_event(
            "user.username.set",
            user_id=str(user.id),
            metadata={"username": username},
        )

        return user

    @staticmethod
    def set_date_of_birth(user_id: str, dob: str) -> bool:
        """Encrypt and store user's date of birth.

        Args:
            user_id: UUID of the user.
            dob: Date string in YYYY-MM-DD format.

        Returns:
            True if successful.

        Raises:
            UserServiceError: If date is invalid or user is under 13.
        """

        try:
            birth_date = date.fromisoformat(dob)
        except ValueError:
            raise UserServiceError(
                "Invalid date format. Use YYYY-MM-DD.",
                code="INVALID_DATE_FORMAT",
            ) from None

        today = datetime.now(timezone.utc).date()
        age = (today - birth_date).days / 365.25

        if age < 13:
            raise UserServiceError(
                "You must be at least 13 years old to use Ziona",
                code="AGE_REQUIREMENT_NOT_MET",
            )

        if age > 120:
            raise UserServiceError(
                "Please enter a valid date of birth",
                code="INVALID_DATE_OF_BIRTH",
            )

        try:
            cipher = Fernet(settings.ENCRYPTION_KEY.encode())
            encrypted = cipher.encrypt(dob.encode())
        except Exception as e:
            logger.error(f"DOB encryption failed: {e}")
            raise UserServiceError(
                "Failed to store date of birth",
                code="ENCRYPTION_FAILED",
            ) from e

        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            raise UserServiceError("User not found", code="USER_NOT_FOUND") from None
        user.encrypted_dob = encrypted
        user.save(update_fields=["encrypted_dob", "updated_at"])

        log_security_event(
            "user.dob.set",
            user_id=str(user.id),
        )

        return True

    @staticmethod
    def get_date_of_birth(user_id: str) -> str | None:
        """Decrypt and return user's date of birth.

        Args:
            user_id: UUID of the user.

        Returns:
            Date string in YYYY-MM-DD format, or None if not set.

        Raises:
            UserServiceError: If user not found or decryption fails.
        """
        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            raise UserServiceError("User not found", code="USER_NOT_FOUND") from None
        if not user.encrypted_dob:
            return None

        try:
            cipher = Fernet(settings.ENCRYPTION_KEY.encode())
            decrypted = cipher.decrypt(bytes(user.encrypted_dob))
            return decrypted.decode()
        except Exception as e:
            logger.error(f"DOB decryption failed for user {user_id}: {e}")
            raise UserServiceError(
                "Failed to retrieve date of birth",
                code="DECRYPTION_FAILED",
            ) from e

import logging
from datetime import date, datetime, timezone

from cryptography.fernet import Fernet
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone as django_timezone

from core.shared.logging import log_security_event
from core.users.models import User, UsernameReservation
from core.users.username_policy import (
    CHANGE_COOLDOWN,
    GRACE_PERIOD,
    grace_changes_remaining,
    normalized_username,
    reserve_released_username,
    username_is_available,
)
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
    def set_username(
        user_id: str, username: str, *, require_initial_selection: bool = False
    ) -> User:
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
            with transaction.atomic():
                try:
                    user = User.objects.select_for_update().get(id=user_id)
                except User.DoesNotExist:
                    raise UserServiceError("User not found", code="USER_NOT_FOUND") from None

                if require_initial_selection and not user.needs_username_selection:
                    raise UserServiceError(
                        "Username selection has already been completed",
                        code="USERNAME_ALREADY_SELECTED",
                    )

                initial_selection = user.needs_username_selection
                if initial_selection:
                    # Keep onboarding's existing format/error contract for OAuth users.
                    from core.authentication.validators import validate_username
                    from core.shared.exceptions import AuthenticationError

                    try:
                        validate_username(username)
                    except AuthenticationError as exc:
                        raise UserServiceError(exc.message, code=exc.code) from exc
                else:
                    try:
                        validate_username_format(username)
                        validate_username_not_reserved(username)
                    except UsernameValidationError as exc:
                        raise UserServiceError(exc.message, code=exc.code) from exc

                if user.username == username:
                    if initial_selection:
                        user.needs_username_selection = False
                        user.save(update_fields=["needs_username_selection", "updated_at"])
                        from django.core.cache import cache

                        cache.delete(f"user_me_data_{user_id}")
                    return user

                now = django_timezone.now()
                in_grace = now < user.created_at + GRACE_PERIOD
                if not initial_selection:
                    if in_grace:
                        if not grace_changes_remaining(user, now=now):
                            raise UserServiceError(
                                "You can change your username at most 3 times in the first 48 hours.",
                                code="RATE_LIMIT_EXCEEDED",
                            )
                    elif (
                        user.last_username_change
                        and now < user.last_username_change + CHANGE_COOLDOWN
                    ):
                        next_change = user.last_username_change + CHANGE_COOLDOWN
                        raise UserServiceError(
                            "You're allowed one username change every 30 days. "
                            f"Next change on {next_change.strftime('%B %d, %Y')}.",
                            code="RATE_LIMIT_EXCEEDED",
                        )

                if not username_is_available(username, owner_id=user.id, now=now):
                    raise UserServiceError("This username is already taken", code="USERNAME_TAKEN")

                old_username = user.username
                # The owner may reclaim their reserved handle; expired claims are also removed.
                UsernameReservation.objects.filter(username=normalized_username(username)).delete()
                if (
                    old_username
                    and not initial_selection
                    and normalized_username(old_username) != normalized_username(username)
                ):
                    reserve_released_username(old_username, user.id, now=now)

                user.username = username
                update_fields = ["username", "updated_at"]
                if initial_selection:
                    user.needs_username_selection = False
                    update_fields.append("needs_username_selection")
                elif in_grace:
                    user.grace_username_changes += 1
                    update_fields.append("grace_username_changes")
                else:
                    user.last_username_change = now
                    update_fields.append("last_username_change")
                user.save(update_fields=update_fields)
        except IntegrityError as exc:
            raise UserServiceError("This username is already taken", code="USERNAME_TAKEN") from exc

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

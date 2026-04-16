"""
Authentication validators — pure validation functions.

No service imports. Only standard library + Django + cryptography.
"""

import logging
import re
from datetime import date, datetime, timezone

from cryptography.fernet import Fernet
from django.conf import settings

from core.shared.exceptions import AuthenticationError

logger = logging.getLogger("core.authentication")


def validate_password(password: str) -> None:
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


def validate_username(username: str) -> None:
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


def validate_and_encrypt_dob(dob_str: str) -> bytes:
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


def generate_unique_username(email: str, name: str) -> str:
    """Generate a unique username from email or name.

    Used for Google OAuth auto-registration.

    Args:
        email: User's email address.
        name: User's full name.

    Returns:
        A unique username string.
    """
    from core.users.models import User

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

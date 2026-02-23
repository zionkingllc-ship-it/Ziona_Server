import re

from core.authentication.services import AuthenticationError


RESERVED_USERNAMES = frozenset({
    "admin", "administrator", "ziona", "support", "help", "info",
    "contact", "root", "system", "mod", "moderator", "official",
    "api", "graphql", "auth", "login", "register", "settings",
    "profile", "account", "null", "undefined", "test", "staff",
})


USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_]{1,28}[a-zA-Z0-9]$")


class UsernameValidationError(Exception):
    """Raised when username validation fails."""

    def __init__(self, message: str, code: str = "INVALID_USERNAME"):
        self.message = message
        self.code = code
        super().__init__(message)


def validate_username_format(username: str) -> None:
    """Validate username meets format requirements.

    Requirements:
    - 3-30 characters
    - Alphanumeric and underscores only
    - Cannot start or end with underscore
    - No consecutive underscores

    Args:
        username: The username to validate.

    Raises:
        UsernameValidationError: If format is invalid.
    """
    if not username or len(username) < 3:
        raise UsernameValidationError(
            "Username must be at least 3 characters",
            code="USERNAME_TOO_SHORT",
        )
    if len(username) > 30:
        raise UsernameValidationError(
            "Username must be 30 characters or fewer",
            code="USERNAME_TOO_LONG",
        )
    if not USERNAME_PATTERN.match(username):
        raise UsernameValidationError(
            "Username can only contain letters, numbers, and underscores. "
            "Cannot start or end with an underscore.",
            code="USERNAME_INVALID_FORMAT",
        )
    if "__" in username:
        raise UsernameValidationError(
            "Username cannot contain consecutive underscores",
            code="USERNAME_INVALID_FORMAT",
        )


def validate_username_not_reserved(username: str) -> None:
    """Check username is not a reserved word.

    Args:
        username: The username to check.

    Raises:
        UsernameValidationError: If username is reserved.
    """
    if username.lower() in RESERVED_USERNAMES:
        raise UsernameValidationError(
            "This username is not available",
            code="USERNAME_RESERVED",
        )

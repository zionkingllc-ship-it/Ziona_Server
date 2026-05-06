"""
Centralized exception hierarchy for the Ziona platform.

All domain-specific exceptions inherit from ZionaError,
which provides a consistent error code and message pattern
for GraphQL error responses.
"""

from enum import Enum


class ErrorCode(str, Enum):
    """Standardized error codes matching the mobile TypeScript spec."""

    TEXT_POST_TOO_LONG = "TEXT_POST_TOO_LONG"
    TEXT_POST_TOO_LONG_WITH_SCRIPTURE = "TEXT_POST_TOO_LONG_WITH_SCRIPTURE"
    VIDEO_TOO_SHORT = "VIDEO_TOO_SHORT"
    VIDEO_TOO_LONG = "VIDEO_TOO_LONG"
    IMAGE_COUNT_INVALID = "IMAGE_COUNT_INVALID"
    INVALID_POST_TYPE = "INVALID_POST_TYPE"
    INVALID_MEDIA_TYPE = "INVALID_MEDIA_TYPE"
    MEDIA_NOT_FOUND = "MEDIA_NOT_FOUND"
    CATEGORY_NOT_FOUND = "CATEGORY_NOT_FOUND"
    INVALID_CATEGORY = "INVALID_CATEGORY"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    INVALID_FILE_TYPE = "INVALID_FILE_TYPE"
    MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"
    VERSION_NOT_AVAILABLE = "VERSION_NOT_AVAILABLE"
    POST_NOT_FOUND = "POST_NOT_FOUND"
    POST_EDIT_WINDOW_EXPIRED = "POST_EDIT_WINDOW_EXPIRED"

    USER_NOT_FOUND = "USER_NOT_FOUND"
    USERNAME_TAKEN = "USERNAME_TAKEN"
    USERNAME_INVALID = "USERNAME_INVALID"
    EMAIL_EXISTS = "EMAIL_EXISTS"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    EMAIL_REGISTERED_WITH_PASSWORD = "EMAIL_REGISTERED_WITH_PASSWORD"
    EMAIL_REGISTERED_WITH_DIFFERENT_PROVIDER = "EMAIL_REGISTERED_WITH_DIFFERENT_PROVIDER"

    ALREADY_LIKED = "ALREADY_LIKED"
    ALREADY_SAVED = "ALREADY_SAVED"
    ALREADY_FOLLOWING = "ALREADY_FOLLOWING"
    COMMENT_NOT_FOUND = "COMMENT_NOT_FOUND"
    COMMENT_TOO_LONG = "COMMENT_TOO_LONG"
    COMMENT_THREAD_TOO_DEEP = "COMMENT_THREAD_TOO_DEEP"
    COMMENT_POST_MISMATCH = "COMMENT_POST_MISMATCH"

    CANNOT_FOLLOW_SELF = "CANNOT_FOLLOW_SELF"

    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
    ENGAGEMENT_SPAM_DETECTED = "ENGAGEMENT_SPAM_DETECTED"

    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    UNAUTHORIZED = "UNAUTHORIZED"
    PERMISSION_DENIED = "PERMISSION_DENIED"

    INVALID_PAGINATION_CURSOR = "INVALID_PAGINATION_CURSOR"

    INVALID_REPORT_REASON = "INVALID_REPORT_REASON"
    INVALID_REPORT_TARGET = "INVALID_REPORT_TARGET"
    DESCRIPTION_REQUIRED = "DESCRIPTION_REQUIRED"
    REPORT_NOT_FOUND = "REPORT_NOT_FOUND"

    FOLDER_NOT_FOUND = "FOLDER_NOT_FOUND"
    FOLDER_ACCESS_DENIED = "FOLDER_ACCESS_DENIED"

    RECIPIENT_NOT_FOUND = "RECIPIENT_NOT_FOUND"

    PASSWORD_ALREADY_EXISTS = "PASSWORD_ALREADY_EXISTS"
    PASSWORD_WEAK = "PASSWORD_WEAK"
    CURRENT_PASSWORD_INCORRECT = "CURRENT_PASSWORD_INCORRECT"
    INVALID_TOKEN = "INVALID_TOKEN"

    VALIDATION_ERROR = "VALIDATION_ERROR"

    NOT_FOUND = "NOT_FOUND"
    DUPLICATE_ENTRY = "DUPLICATE_ENTRY"

    NOT_AUTHORIZED = "NOT_AUTHORIZED"
    INVALID_ADMIN_TOKEN = "INVALID_ADMIN_TOKEN"
    ACCOUNT_SUSPENDED = "ACCOUNT_SUSPENDED"
    USER_ALREADY_SUSPENDED = "USER_ALREADY_SUSPENDED"
    USER_ALREADY_WARNED = "USER_ALREADY_WARNED"
    USER_CANNOT_DELETE_SELF = "USER_CANNOT_DELETE_SELF"

    CIRCLE_EDIT_COOLDOWN = "CIRCLE_EDIT_COOLDOWN"
    CIRCLE_NOT_FOUND = "CIRCLE_NOT_FOUND"
    CIRCLE_HAS_ACTIVE_MEMBERS = "CIRCLE_HAS_ACTIVE_MEMBERS"
    DUPLICATE_NAME = "DUPLICATE_NAME"

    ANCHOR_NOT_FOUND = "ANCHOR_NOT_FOUND"
    ANCHOR_SCHEDULE_PAST_DATE = "ANCHOR_SCHEDULE_PAST_DATE"
    ANCHOR_ALREADY_POSTED = "ANCHOR_ALREADY_POSTED"
    ANCHOR_NOT_SCHEDULED = "ANCHOR_NOT_SCHEDULED"

    REPORT_ALREADY_REVIEWED = "REPORT_ALREADY_REVIEWED"

    CONTACT_NOT_FOUND = "CONTACT_NOT_FOUND"


class ZionaError(Exception):
    """Base exception for all Ziona domain errors.

    Attributes:
        message: Human-readable error description.
        code: Machine-readable error code from ErrorCode enum.
        extensions: Optional dict with additional error context.
    """

    def __init__(
        self,
        message: str,
        code: str = ErrorCode.VALIDATION_ERROR,
        extensions: dict | None = None,
    ):
        self.message = message
        self.code = code
        self.extensions = extensions or {}
        super().__init__(message)


class AuthenticationError(ZionaError):
    """Raised when authentication operations fail."""

    def __init__(
        self,
        message: str,
        code: str = "AUTH_ERROR",
        details: dict | None = None,
    ):
        super().__init__(message, code, extensions=details)

    @property
    def details(self) -> dict:
        """Alias for extensions to support legacy auth code."""
        return self.extensions


class PostError(ZionaError):
    """Raised when post operations fail."""


class EngagementError(ZionaError):
    """Raised when engagement operations fail."""


class FollowError(ZionaError):
    """Raised when follow operations fail."""


class FeedError(ZionaError):
    """Raised when feed operations fail."""


class ProfileError(ZionaError):
    """Raised when profile operations fail."""


class ModerationError(ZionaError):
    """Raised when moderation operations fail."""


class BookmarkError(ZionaError):
    """Raised when bookmark operations fail."""


class ShareError(ZionaError):
    """Raised when share operations fail."""


class AdminError(ZionaError):
    """Raised when admin dashboard operations fail."""

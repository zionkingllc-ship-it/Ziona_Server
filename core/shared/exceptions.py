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
    VIDEO_TOO_SHORT = "VIDEO_TOO_SHORT"
    VIDEO_TOO_LONG = "VIDEO_TOO_LONG"
    IMAGE_COUNT_INVALID = "IMAGE_COUNT_INVALID"
    INVALID_POST_TYPE = "INVALID_POST_TYPE"
    INVALID_CATEGORY = "INVALID_CATEGORY"
    MEDIA_TYPE_MISMATCH = "MEDIA_TYPE_MISMATCH"
    POST_NOT_FOUND = "POST_NOT_FOUND"
    POST_EDIT_WINDOW_EXPIRED = "POST_EDIT_WINDOW_EXPIRED"

    USER_NOT_FOUND = "USER_NOT_FOUND"
    USERNAME_TAKEN = "USERNAME_TAKEN"
    USERNAME_INVALID = "USERNAME_INVALID"
    EMAIL_EXISTS = "EMAIL_EXISTS"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"

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
    INVALID_MEDIA_TYPE = "INVALID_MEDIA_TYPE"

    RECIPIENT_NOT_FOUND = "RECIPIENT_NOT_FOUND"

    PASSWORD_ALREADY_EXISTS = "PASSWORD_ALREADY_EXISTS"
    PASSWORD_WEAK = "PASSWORD_WEAK"
    CURRENT_PASSWORD_INCORRECT = "CURRENT_PASSWORD_INCORRECT"
    INVALID_TOKEN = "INVALID_TOKEN"

    VALIDATION_ERROR = "VALIDATION_ERROR"


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

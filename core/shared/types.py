from enum import Enum

import strawberry


@strawberry.enum
class PostType(str, Enum):
    TEXT = "TEXT"
    MEDIA = "MEDIA"
    BIBLE = "BIBLE"


@strawberry.enum
class MediaType(str, Enum):
    IMAGE = "IMAGE"
    VIDEO = "VIDEO"


@strawberry.type
class ErrorType:
    code: str  # Error code (e.g., "INVALID_POST_TYPE")
    message: str  # Human-readable message
    field: str | None = None  # Field that caused error
    details: strawberry.scalars.JSON | None = None  # JSON object for additional data


@strawberry.type
class ScriptureVerse:
    """A single verse inside a scripture reference."""

    number: int
    text: str

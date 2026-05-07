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


@strawberry.type
class PageInfo:
    """Reusable pagination metadata. Used by all paginated GraphQL responses."""

    has_next_page: bool = strawberry.field(name="hasNextPage")
    total_count: int = strawberry.field(name="totalCount")
    current_page: int = strawberry.field(name="currentPage")

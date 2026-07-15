"""Engagement GraphQL schema package.

Re-exports the public surface of the former core/engagement/schema.py module.
BookmarkFolderType MUST stay importable from this path — posts/feed schema
reference it via strawberry.lazy("core.engagement.schema").
"""

from core.engagement.schema.mutations import EngagementMutations
from core.engagement.schema.queries import EngagementQueries
from core.engagement.schema.types import (
    BookmarkFolderType,
    CommentType,
    FriendType,
)

__all__ = [
    "BookmarkFolderType",
    "CommentType",
    "EngagementMutations",
    "EngagementQueries",
    "FriendType",
]

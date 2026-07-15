"""Engagement — hidden operations.

Split from the former core/engagement/services.py (no behavior change).
"""
import logging
import re

from django.db.models import Q

from core.engagement.hidden_content import (
    hide_post_for_user,
    unhide_post_for_user,
)
from core.engagement.models import (
    HiddenPost,
)
from core.posts.models import Post

logger = logging.getLogger("core.engagement")

COMMENT_MAX_LENGTH = 500
COMMENT_MAX_THREAD_DEPTH = 3
MENTION_REGEX = re.compile(r"@(\w{3,30})")

DEFAULT_BOOKMARK_FOLDERS = [
    "All",
]


def hide_post(user_id: str, post_id: str) -> bool:
    """Hide a post from the current user's feed.

    Enforces a 1,000 post limit per user, using a sliding window
    to automatically delete the oldest constraint.
    """
    return hide_post_for_user(user_id, post_id)


def unhide_post(user_id: str, post_id: str) -> bool:
    """Unhide a previously hidden post."""
    return unhide_post_for_user(user_id, post_id)


def get_hidden_posts(
    user_id: str, cursor: str | None = None, limit: int = 20
) -> tuple[list[Post], str | None, bool]:
    """Get paginated list of hidden posts for a user.

    Returns:
        Tuple of (posts, next_cursor, has_more)
    """

    limit = min(limit, 50)

    # Order by HiddenPost.created_at (when it was hidden) instead of Post.created_at
    qs = (
        HiddenPost.objects.filter(user_id=user_id, post__deleted_at__isnull=True)
        .select_related("post")
        .order_by("-created_at", "-id")
    )

    if cursor:
        try:
            # The cursor value passed from the frontend is the Post ID
            cursor_hide = (
                HiddenPost.objects.filter(user_id=user_id, post_id=cursor)
                .values("created_at", "id")
                .first()
            )
            if cursor_hide:
                qs = qs.filter(
                    Q(created_at__lt=cursor_hide["created_at"])
                    | Q(
                        created_at=cursor_hide["created_at"],
                        id__lt=cursor_hide["id"],
                    )
                )
        except Exception:  # noqa: BLE001
            logger.debug("Failed to apply hidden post cursor: invalid cursor_id")

    hides = list(qs[: limit + 1])
    has_more = len(hides) > limit
    hides = hides[:limit]

    posts = [h.post for h in hides]
    next_cursor = str(hides[-1].post_id) if has_more and hides else None

    return posts, next_cursor, has_more

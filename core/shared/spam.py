"""
Spam detection utilities for engagement actions.

Tracks rapid like/unlike toggles and blocks abusive patterns.

Uses an atomic Lua script (LuaLimiter.check_spam) that combines:
  1. Cooldown-key existence check
  2. Sliding-window toggle count
  3. Conditional cooldown activation

...into a single Redis EVAL command, replacing the previous 5-7 command
sequence (EXISTS + TTL + ZREMRANGEBYSCORE + ZADD + ZCARD + EXPIRE + SETEX).
"""

import logging

from core.shared.exceptions import EngagementError, ErrorCode

logger = logging.getLogger("core.shared")


SPAM_TOGGLE_LIMIT = 5
SPAM_WINDOW_SECONDS = 10
COOLDOWN_SECONDS = 60


def check_engagement_spam(user_id: str, post_id: str, action: str = "like") -> None:
    """Check if a user is spamming engagement actions.

    Tracks the number of like/unlike toggles on a specific post within a
    short window. Blocks the user for COOLDOWN_SECONDS if the threshold
    is exceeded.

    Costs exactly 1 Redis command (EVAL of the spam Lua script).

    Args:
        user_id: UUID of the acting user.
        post_id: UUID of the target post.
        action: Type of action ("like", "unlike").

    Raises:
        EngagementError: With ENGAGEMENT_SPAM_DETECTED code if spam detected.
    """
    from core.shared.redis_lua import LuaLimiter

    is_spamming, retry_after = LuaLimiter.check_spam(
        user_id=user_id,
        post_id=post_id,
        action=action,
        window_seconds=SPAM_WINDOW_SECONDS,
        max_toggles=SPAM_TOGGLE_LIMIT,
        cooldown_seconds=COOLDOWN_SECONDS,
    )

    if is_spamming:
        logger.warning(
            "engagement_spam_detected",
            extra={
                "user_id": user_id,
                "post_id": post_id,
                "action": action,
                "retry_after": retry_after,
            },
        )
        raise EngagementError(
            message=f"Temporarily blocked due to spam. Try again in {retry_after} seconds.",
            code=ErrorCode.ENGAGEMENT_SPAM_DETECTED,
            extensions={"retry_after": retry_after},
        )

"""
Spam detection utilities for engagement actions.

Tracks rapid like/unlike toggles and blocks abusive patterns.
Uses Redis to maintain a short-lived action log per user per post.
"""

import logging
import time

from core.shared.exceptions import EngagementError, ErrorCode

logger = logging.getLogger("core.shared")


SPAM_TOGGLE_LIMIT = 5
SPAM_WINDOW_SECONDS = 10
COOLDOWN_SECONDS = 60


def check_engagement_spam(user_id: str, post_id: str, action: str = "like") -> None:
    """Check if a user is spamming engagement actions.

    Tracks the number of like/unlike toggles on a specific post
    within a short window. Blocks the user if the threshold is exceeded.

    Args:
        user_id: UUID of the acting user.
        post_id: UUID of the target post.
        action: Type of action (like, unlike).

    Raises:
        EngagementError: With ENGAGEMENT_SPAM_DETECTED code if spam detected.
    """
    try:
        from django_redis import get_redis_connection

        redis_conn = get_redis_connection("default")

        cooldown_key = f"spam:cooldown:{user_id}"
        if redis_conn.exists(cooldown_key):
            ttl = redis_conn.ttl(cooldown_key)
            raise EngagementError(
                message=f"Temporarily blocked due to spam. Try again in {ttl} seconds.",
                code=ErrorCode.ENGAGEMENT_SPAM_DETECTED,
                extensions={"retry_after": ttl},
            )

        toggle_key = f"spam:toggle:{user_id}:{post_id}"
        now = time.time()
        window_start = now - SPAM_WINDOW_SECONDS

        pipeline = redis_conn.pipeline()
        pipeline.zremrangebyscore(toggle_key, 0, window_start)
        pipeline.zadd(toggle_key, {f"{action}:{now}": now})
        pipeline.zcard(toggle_key)
        pipeline.expire(toggle_key, SPAM_WINDOW_SECONDS)
        results = pipeline.execute()

        toggle_count = results[2]

        if toggle_count > SPAM_TOGGLE_LIMIT:
            redis_conn.setex(cooldown_key, COOLDOWN_SECONDS, "1")

            logger.warning(
                "engagement_spam_detected",
                extra={
                    "user_id": user_id,
                    "post_id": post_id,
                    "action": action,
                    "toggle_count": toggle_count,
                },
            )

            raise EngagementError(
                message="Engagement spam detected. You are temporarily blocked.",
                code=ErrorCode.ENGAGEMENT_SPAM_DETECTED,
                extensions={"retry_after": COOLDOWN_SECONDS},
            )

    except EngagementError:
        raise
    except Exception:
        logger.warning("Spam detection unavailable — Redis connection failed")

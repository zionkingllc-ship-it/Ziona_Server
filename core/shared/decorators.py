"""
Rate limiting decorator for service-layer methods.

Uses Redis sliding window to enforce per-user rate limits
on engagement actions (likes, comments, saves, follows, reports).
"""

import functools
import logging
import time

from core.shared.exceptions import EngagementError, ErrorCode

logger = logging.getLogger("core.shared")


def rate_limit(max_requests: int, window_seconds: int):
    """Decorator to enforce per-user rate limiting on service methods.

    Expects the decorated function's first positional argument (after self/cls)
    to be `user_id`. Uses Redis sorted sets for sliding window counting.

    Args:
        max_requests: Maximum allowed requests in the window.
        window_seconds: Time window in seconds.

    Raises:
        EngagementError: With RATE_LIMIT_EXCEEDED code when limit is hit.
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            user_id = kwargs.get("user_id") or (args[0] if args else None)

            if not user_id:
                return func(*args, **kwargs)

            key = f"ratelimit:{func.__qualname__}:{user_id}"

            try:
                from django_redis import get_redis_connection

                redis_conn = get_redis_connection("default")
                now = time.time()
                window_start = now - window_seconds

                pipeline = redis_conn.pipeline()
                pipeline.zremrangebyscore(key, 0, window_start)
                pipeline.zcard(key)
                pipeline.zadd(key, {str(now): now})
                pipeline.expire(key, window_seconds)
                results = pipeline.execute()

                current_count = results[1]
                if current_count >= max_requests:
                    oldest = redis_conn.zrange(key, 0, 0, withscores=True)
                    retry_after = window_seconds
                    if oldest:
                        retry_after = int(oldest[0][1] + window_seconds - now) + 1
                    retry_after = max(retry_after, 1)

                    raise EngagementError(
                        message=f"Rate limit exceeded. Try again in {retry_after} seconds.",
                        code=ErrorCode.RATE_LIMIT_EXCEEDED,
                        extensions={"retry_after": retry_after},
                    )
            except EngagementError:
                raise
            except Exception:
                logger.warning("Rate limiting unavailable — Redis connection failed")

            return func(*args, **kwargs)

        return wrapper

    return decorator

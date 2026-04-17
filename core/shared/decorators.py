"""
Rate limiting decorator for service-layer methods.

Uses an atomic Lua script (via LuaLimiter) to enforce per-user rate limits
on engagement actions (likes, comments, saves, follows, reports).

Each invocation costs exactly 1 Redis command instead of the previous
4-command pipeline (ZREMRANGEBYSCORE + ZCARD + ZADD + EXPIRE).
"""

import functools
import logging

from core.shared.exceptions import EngagementError, ErrorCode

logger = logging.getLogger("core.shared")


def rate_limit(max_requests: int, window_seconds: int):
    """Decorator to enforce per-user rate limiting on service methods.

    Expects the decorated function's first positional argument (after self/cls)
    to be `user_id`. Uses an atomic Lua sliding-window script for counting.

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

            from core.shared.redis_lua import LuaLimiter

            # Single atomic Redis command — replaces the old 4-command pipeline
            is_limited, retry_after = LuaLimiter.check_rate_limit(key, max_requests, window_seconds)

            if is_limited:
                raise EngagementError(
                    message=f"Rate limit exceeded. Try again in {retry_after} seconds.",
                    code=ErrorCode.RATE_LIMIT_EXCEEDED,
                    extensions={"retry_after": retry_after},
                )

            return func(*args, **kwargs)

        return wrapper

    return decorator

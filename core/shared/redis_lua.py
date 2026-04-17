"""
Atomic Redis rate limiting and spam detection via Lua scripts.

WHY LUA SCRIPTS?
  A standard Python pipeline (ZREMRANGEBYSCORE + ZCARD + ZADD + EXPIRE) costs
  4 Upstash requests per call. An EVAL of a Lua script costs exactly 1 request,
  regardless of how many Redis commands the script runs internally, because the
  script executes atomically on the Redis server.

  Result: 75% reduction in Upstash command consumption.
"""

import logging
import time

logger = logging.getLogger("core.shared")


# ---------------------------------------------------------------------------
# Lua Script 1: Sliding-Window Rate Limit
# ---------------------------------------------------------------------------
# Collapses ZREMRANGEBYSCORE + ZCARD + ZADD + EXPIRE → 1 EVAL command.
# KEYS[1] = bucket key
# ARGV: now(float), window(secs), max_requests(int), unique_member(str)
# Returns: [is_limited(0|1), current_count, retry_after_secs]
# ---------------------------------------------------------------------------
_RATE_LIMIT_LUA = """
local key    = KEYS[1]
local now    = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit  = tonumber(ARGV[3])
local member = ARGV[4]

redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local count = redis.call('ZCARD', key)

if count >= limit then
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local retry  = window
    if #oldest > 0 then
        retry = math.max(math.ceil(tonumber(oldest[2]) + window - now), 1)
    end
    return {1, count, retry}
end

redis.call('ZADD', key, now, member)
redis.call('EXPIRE', key, window + 1)
return {0, count + 1, 0}
"""


# ---------------------------------------------------------------------------
# Lua Script 2: Engagement Spam Check (Cooldown + Sliding Window)
# ---------------------------------------------------------------------------
# Collapses EXISTS + TTL + ZREMRANGEBYSCORE + ZADD + ZCARD + EXPIRE + SETEX
# → 1 EVAL command.
# KEYS[1] = cooldown key, KEYS[2] = toggle sliding-window key
# ARGV: now(float), window(secs), max_toggles(int), cooldown_ttl(secs), action_id(str)
# Returns: [is_spamming(0|1), retry_after_secs]
# ---------------------------------------------------------------------------
_SPAM_CHECK_LUA = """
local cooldown_key = KEYS[1]
local toggle_key   = KEYS[2]
local now          = tonumber(ARGV[1])
local window       = tonumber(ARGV[2])
local max_toggles  = tonumber(ARGV[3])
local cooldown_ttl = tonumber(ARGV[4])
local action_id    = ARGV[5]

-- 1. Check if already in cooldown
local ttl = redis.call('TTL', cooldown_key)
if ttl > 0 then
    return {1, ttl}
end

-- 2. Sliding window: clean old toggles, add this one, count
redis.call('ZREMRANGEBYSCORE', toggle_key, 0, now - window)
redis.call('ZADD', toggle_key, now, action_id)
local count = redis.call('ZCARD', toggle_key)
redis.call('EXPIRE', toggle_key, window + 1)

-- 3. Trigger cooldown if spam threshold exceeded
if count > max_toggles then
    redis.call('SETEX', cooldown_key, cooldown_ttl, '1')
    return {1, cooldown_ttl}
end

return {0, 0}
"""


class LuaLimiter:
    """
    Atomic Redis rate limiter and spam detector.

    Each method issues exactly ONE Redis EVAL command, regardless of how many
    Redis operations the Lua script performs internally. This is the canonical
    production pattern for rate limiting on command-metered Redis services
    like Upstash.

    Fail-open policy: if Redis is unavailable, requests are allowed through.
    The app must never crash because of a cache layer failure.
    """

    @staticmethod
    def check_rate_limit(key: str, max_requests: int, window_seconds: int) -> tuple[bool, int]:
        """
        Atomic sliding-window rate limit check. Costs exactly 1 Redis command.

        Args:
            key: Redis key for this rate limit bucket.
            max_requests: Maximum allowed requests in the time window.
            window_seconds: Size of the sliding window in seconds.

        Returns:
            (is_limited, retry_after_seconds)
        """
        try:
            from django_redis import get_redis_connection

            redis_conn = get_redis_connection("default")
            now = time.time()
            # Microsecond precision prevents member collisions in the sorted set
            member = f"{now:.6f}"
            results = redis_conn.eval(
                _RATE_LIMIT_LUA, 1, key, now, window_seconds, max_requests, member
            )
            return bool(results[0]), int(results[2])
        except Exception as e:
            logger.warning("LuaLimiter.check_rate_limit failed: %s — failing open", e)
            return False, 0

    @staticmethod
    def check_spam(
        user_id: str,
        post_id: str,
        action: str,
        window_seconds: int = 10,
        max_toggles: int = 5,
        cooldown_seconds: int = 60,
    ) -> tuple[bool, int]:
        """
        Atomic engagement spam detection. Costs exactly 1 Redis command.

        Combines cooldown-key existence check + sliding-window toggle count +
        conditional cooldown activation into a single atomic EVAL.

        Args:
            user_id: UUID of the acting user.
            post_id: UUID of the target post.
            action: Action type string (e.g. "like", "unlike").
            window_seconds: Sliding window size for toggle counting.
            max_toggles: Max toggles allowed before cooldown triggers.
            cooldown_seconds: Duration of the spam cooldown penalty.

        Returns:
            (is_spamming, retry_after_seconds)
        """
        try:
            from django_redis import get_redis_connection

            redis_conn = get_redis_connection("default")
            cooldown_key = f"spam:cooldown:{user_id}"
            toggle_key = f"spam:toggle:{user_id}:{post_id}"
            now = time.time()
            action_id = f"{action}:{now:.6f}"

            results = redis_conn.eval(
                _SPAM_CHECK_LUA,
                2,  # number of KEYS
                cooldown_key,
                toggle_key,
                now,
                window_seconds,
                max_toggles,
                cooldown_seconds,
                action_id,
            )
            return bool(results[0]), int(results[1])
        except Exception as e:
            logger.warning("LuaLimiter.check_spam failed: %s — failing open", e)
            return False, 0

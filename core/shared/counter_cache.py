"""
Lightweight Redis counter cache for platform-wide statistics.

Eliminates expensive COUNT(*) full-table scans by maintaining an atomic
increment/decrement counter in Redis.  Falls back to a live DB count when
the cache is cold (first access or after TTL expiry).

Redis cost: Each inc/dec is 1 INCRBY command.  The fallback COUNT(*)
runs at most once per ``COUNTER_TTL`` seconds (default: 1 hour).

Usage::

    from core.shared.counter_cache import post_counter
    post_counter.increment()           # after create_post
    post_counter.decrement()           # after delete_post
    total = post_counter.get()         # fast read — no DB hit
"""

import logging

logger = logging.getLogger("core.shared")

# 1 hour — long enough to amortise the cold-start COUNT, short enough to
# self-correct if the counter ever drifts due to a missed inc/dec.
COUNTER_TTL = 3600


class _CounterCache:
    """Generic Redis-backed counter with DB fallback.

    Designed for exactly one pattern: a single integer value that can be
    incremented or decremented cheaply, with an infrequent DB ``COUNT``
    to seed / resync the counter.

    Thread-safe by virtue of Redis's single-threaded command execution.
    Fail-open: if Redis is unavailable, falls through to a DB count.
    """

    def __init__(self, cache_key: str, ttl: int = COUNTER_TTL):
        self._cache_key = cache_key
        self._ttl = ttl

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self) -> int:
        """Return the current count (from Redis, or a live DB count on miss)."""
        try:
            from django.core.cache import cache

            val = cache.get(self._cache_key)
            if val is not None:
                return int(val)
        except Exception:
            logger.debug("counter_cache miss (redis unavailable): %s", self._cache_key)

        # Cache miss — seed from DB and store.
        return self._seed_from_db()

    def increment(self, delta: int = 1) -> None:
        """Atomically increment the counter.  Cheap — 1 Redis command."""
        try:
            from django.core.cache import cache

            # If key doesn't exist yet, seed from DB first so the delta
            # is applied to an accurate baseline.
            if cache.get(self._cache_key) is None:
                self._seed_from_db()
            cache.incr(self._cache_key, delta)
        except Exception:
            logger.debug("counter_cache increment failed: %s", self._cache_key)

    def decrement(self, delta: int = 1) -> None:
        """Atomically decrement the counter.  Cheap — 1 Redis command."""
        try:
            from django.core.cache import cache

            if cache.get(self._cache_key) is None:
                self._seed_from_db()
            # django.core.cache.decr is the inverse of incr.
            cache.decr(self._cache_key, delta)
        except Exception:
            logger.debug("counter_cache decrement failed: %s", self._cache_key)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _seed_from_db(self) -> int:
        """Run the (expensive) DB count once and store in Redis."""
        from core.posts.models import Post

        count = Post.objects.filter(deleted_at__isnull=True).count()
        try:
            from django.core.cache import cache

            cache.set(self._cache_key, count, timeout=self._ttl)
        except Exception:
            logger.debug("counter_cache seed failed: %s", self._cache_key)
        return count


# Singleton instance — import and use directly.
post_counter = _CounterCache("stats:active_post_count")

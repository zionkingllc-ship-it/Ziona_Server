"""
Caching layer for engagement domain, specifically for Hidden Posts.

Implements intelligent cache warming and rehydration from PostgreSQL
to Redis using sentinel values to prevent infinite cache-miss loops.
"""

import logging

from django_redis import get_redis_connection

from core.engagement.models import HiddenPost

logger = logging.getLogger("core.engagement")

REDIS_KEY_PREFIX = "hidden_posts"
CACHE_TTL = 7 * 24 * 60 * 60  # 7 days


class EngagementCache:
    """Manages Redis caching for engagement states (like hidden posts)."""

    @staticmethod
    def _get_key(user_id: str) -> str:
        return f"{REDIS_KEY_PREFIX}:{user_id}"

    @classmethod
    def warm_hidden_posts_cache(cls, user_id: str) -> None:
        """
        Rebuilds the Redis set of hidden posts from PostgreSQL.
        Uses a sentinel value "_INIT_" to track that the cache is warm
        even if the user has 0 hidden posts, preventing cache-miss loops.
        """
        redis_conn = get_redis_connection("default")
        key = cls._get_key(user_id)

        try:
            # Fetch from Postgres
            hidden_post_ids = list(
                HiddenPost.objects.filter(user_id=user_id).values_list("post_id", flat=True)
            )

            # Rebuild Redis pipeline
            pipeline = redis_conn.pipeline()
            pipeline.delete(key)
            pipeline.sadd(key, "_INIT_")  # Sentinel value

            if hidden_post_ids:
                # Convert UUIDs to strings
                str_ids = [str(pid) for pid in hidden_post_ids]
                pipeline.sadd(key, *str_ids)

            pipeline.expire(key, CACHE_TTL)
            pipeline.execute()

            logger.debug(f"Warmed hidden posts cache for user {user_id}")
        except Exception as e:
            logger.error(f"Failed to warm hidden posts cache for user {user_id}: {e}")

    @classmethod
    def is_post_hidden(cls, user_id: str, post_id: str) -> bool:
        """
        Check if a post is hidden by the user.
        Warm cache on miss.
        """
        redis_conn = get_redis_connection("default")
        key = cls._get_key(user_id)

        try:
            # Check if cache exists (the _INIT_ sentinel guarantees this)
            if not redis_conn.exists(key):
                cls.warm_hidden_posts_cache(user_id)

            return bool(redis_conn.sismember(key, str(post_id)))
        except Exception as e:
            logger.error(f"Redis error checking hidden post: {e}")
            # Fallback to postgres
            return HiddenPost.objects.filter(user_id=user_id, post_id=post_id).exists()

    @classmethod
    def get_hidden_post_ids(cls, user_id: str) -> set[str]:
        """
        Get all hidden post IDs for a user.
        Warm cache on miss.
        """
        redis_conn = get_redis_connection("default")
        key = cls._get_key(user_id)

        try:
            if not redis_conn.exists(key):
                cls.warm_hidden_posts_cache(user_id)

            members = redis_conn.smembers(key)
            # Filter out and decode members
            hidden_ids = {m.decode("utf-8") for m in members if m.decode("utf-8") != "_INIT_"}

            # Reset TTL on read
            redis_conn.expire(key, CACHE_TTL)

            return hidden_ids
        except Exception as e:
            logger.error(f"Redis error getting hidden posts: {e}")
            # Fallback to postgres
            return {
                str(pid)
                for pid in HiddenPost.objects.filter(user_id=user_id).values_list(
                    "post_id", flat=True
                )
            }

    @classmethod
    def mark_post_hidden(cls, user_id: str, post_id: str) -> None:
        """Add post to cache directly without full warm."""
        redis_conn = get_redis_connection("default")
        key = cls._get_key(user_id)
        try:
            if redis_conn.exists(key):
                redis_conn.sadd(key, str(post_id))
        except Exception as e:
            logger.error(f"Redis error marking hidden post: {e}")

    @classmethod
    def unmark_post_hidden(cls, user_id: str, post_id: str) -> None:
        """Remove post from cache."""
        redis_conn = get_redis_connection("default")
        key = cls._get_key(user_id)
        try:
            if redis_conn.exists(key):
                redis_conn.srem(key, str(post_id))
        except Exception as e:
            logger.error(f"Redis error unmarking hidden post: {e}")

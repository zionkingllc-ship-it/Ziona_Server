"""
Feed background tasks — cache invalidation, feed pre-generation, and fan-out.

These tasks run asynchronously via Celery to keep feed caches fresh
after content changes.

Fan-out-on-Write Architecture (Industry Standard)
--------------------------------------------------
When a user creates a post, ``fan_out_post_to_inboxes`` pushes the post ID
into every follower's personal Redis "feed inbox" list.  When a follower
later opens their feed, the server reads from this pre-built list instead
of running an expensive DB ranking query.

Redis cost optimisation (Upstash budget):
- Fan-out uses a single Lua EVAL per follower chunk (500 followers) to
  batch LPUSH + LTRIM into 1 Redis command per chunk.
- Removal uses pipelined LREM (1 command per follower chunk).
- Inbox reads use a single LRANGE (1 command per feed request).

Celebrity threshold: Users with > CELEBRITY_FOLLOWER_THRESHOLD followers
are excluded from fan-out; their posts are pulled on-demand at read time
to avoid flooding Redis with millions of writes.
"""

import logging
import time

from celery import shared_task

logger = logging.getLogger("core.feed")

# Posts older than this are never fanned out (guards against backfill storms).
MAX_FANOUT_AGE_HOURS = 24

# Maximum inbox length per user — older entries are auto-trimmed.
INBOX_MAX_LENGTH = 500

# Users above this follower count use pull-on-demand instead of push.
CELEBRITY_FOLLOWER_THRESHOLD = 50_000

# How many follower IDs to process per Lua EVAL batch.
_FANOUT_CHUNK_SIZE = 500


# ---------------------------------------------------------------------------
# Lua Script: Batched LPUSH + LTRIM (1 EVAL = 1 Upstash command)
# ---------------------------------------------------------------------------
# KEYS = [inbox_key_1, inbox_key_2, ..., inbox_key_N]
# ARGV = [post_id, max_length]
# Pushes post_id to the head of every inbox key and trims to max_length.
# ---------------------------------------------------------------------------
_FANOUT_LUA = """
local post_id    = ARGV[1]
local max_length = tonumber(ARGV[2])
for i = 1, #KEYS do
    redis.call('LPUSH', KEYS[i], post_id)
    redis.call('LTRIM', KEYS[i], 0, max_length - 1)
end
return #KEYS
"""


@shared_task(name="feed.invalidate_followers_feed_cache")
def invalidate_followers_feed_cache(user_id: str) -> None:
    """Invalidate feed caches for all followers of a user.

    Called when a user creates, updates, or deletes a post
    so that their followers' feed caches reflect the change.

    Args:
        user_id: UUID of the content creator.
    """
    try:
        from django.core.cache import cache

        from core.follows.selectors import FollowSelector

        follower_ids = FollowSelector.get_follower_ids(user_id)

        if not follower_ids:
            return

        cache_keys = [f"feed:following:{fid}" for fid in follower_ids]
        cache_keys.append(f"feed:for_you:{user_id}")
        cache.delete_many(cache_keys)

        logger.info(
            "feed_cache_invalidated",
            extra={
                "user_id": user_id,
                "followers_affected": len(follower_ids),
            },
        )
    except Exception:
        logger.warning(
            "feed_cache_invalidation_failed",
            extra={"user_id": user_id},
            exc_info=True,
        )


@shared_task(name="feed.warm_feed_cache")
def warm_feed_cache(user_id: str) -> None:
    """Pre-generate and cache a user's feed.

    Called after cache invalidation to proactively fill the cache.

    Args:
        user_id: UUID of the user whose feed to pre-generate.
    """
    try:
        from core.feed.services import FeedService

        FeedService.get_for_you_feed(user_id=user_id, limit=20)
        logger.info("feed_cache_warmed", extra={"user_id": user_id})
    except Exception:
        logger.warning(
            "feed_cache_warm_failed",
            extra={"user_id": user_id},
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# Phase 3: Fan-out-on-Write
# ---------------------------------------------------------------------------


@shared_task(
    name="feed.fan_out_post_to_inboxes",
    bind=True,
    max_retries=3,
    default_retry_delay=10,
)
def fan_out_post_to_inboxes(self, post_id: str, author_id: str) -> None:
    """Fan out a new post ID into followers' Redis feed inboxes.

    Uses chunked Lua EVAL to batch LPUSH + LTRIM operations, keeping
    Redis command count low (critical for Upstash budget).

    Celebrity accounts (>50k followers) are skipped — their posts are
    pulled on-demand at feed read time to avoid write amplification.

    Args:
        post_id: UUID of the newly created post.
        author_id: UUID of the post author.
    """
    try:
        from core.follows.selectors import FollowSelector
        from core.posts.models import Post

        # Guard: don't fan-out deleted or very old posts.
        post = (
            Post.objects.filter(id=post_id, deleted_at__isnull=True)
            .only("id", "created_at")
            .first()
        )
        if not post:
            logger.debug("fan_out skipped: post %s not found or deleted", post_id)
            return

        age_hours = (time.time() - post.created_at.timestamp()) / 3600
        if age_hours > MAX_FANOUT_AGE_HOURS:
            logger.debug("fan_out skipped: post %s too old (%.1fh)", post_id, age_hours)
            return

        follower_ids = FollowSelector.get_follower_ids(author_id)
        if not follower_ids:
            return

        # Celebrity check — skip fan-out for mega-accounts.
        if len(follower_ids) > CELEBRITY_FOLLOWER_THRESHOLD:
            logger.info(
                "fan_out_skipped_celebrity",
                extra={
                    "author_id": author_id,
                    "follower_count": len(follower_ids),
                    "post_id": post_id,
                },
            )
            # Store in the celebrity sorted set instead (scored by timestamp
            # for chronological merge at read time).
            try:
                from django_redis import get_redis_connection

                redis_conn = get_redis_connection("default")
                celeb_key = f"feed:celebrity:{author_id}"
                redis_conn.zadd(celeb_key, {post_id: post.created_at.timestamp()})
                # Keep only the most recent 200 celebrity posts.
                redis_conn.zremrangebyrank(celeb_key, 0, -201)
                redis_conn.expire(celeb_key, 86400 * 7)  # 7-day TTL
            except Exception:
                logger.warning("celebrity_set_update_failed", exc_info=True)
            return

        # Chunked fan-out via Lua script — 1 EVAL per chunk.
        try:
            from django_redis import get_redis_connection

            redis_conn = get_redis_connection("default")

            for i in range(0, len(follower_ids), _FANOUT_CHUNK_SIZE):
                chunk = follower_ids[i : i + _FANOUT_CHUNK_SIZE]
                inbox_keys = [f"feed:inbox:{fid}" for fid in chunk]
                redis_conn.eval(
                    _FANOUT_LUA,
                    len(inbox_keys),
                    *inbox_keys,
                    post_id,
                    INBOX_MAX_LENGTH,
                )

            # Set a TTL on each inbox to auto-expire abandoned accounts.
            # Done outside the Lua to keep the script simple.
            # Use a pipeline to batch the EXPIRE commands (1 round-trip).
            pipe = redis_conn.pipeline(transaction=False)
            for fid in follower_ids:
                pipe.expire(f"feed:inbox:{fid}", 86400 * 7)  # 7-day TTL
            pipe.execute()

        except Exception:
            logger.warning("fan_out_redis_failed", exc_info=True)
            # Don't retry on Redis failures — the DB fallback will serve
            # the feed correctly, just slightly slower.
            return

        logger.info(
            "fan_out_complete",
            extra={
                "post_id": post_id,
                "author_id": author_id,
                "followers_fanned": len(follower_ids),
            },
        )

    except Exception as exc:
        logger.warning("fan_out_failed", extra={"post_id": post_id}, exc_info=True)
        raise self.retry(exc=exc) from exc


@shared_task(name="feed.remove_post_from_inboxes")
def remove_post_from_inboxes(post_id: str, author_id: str) -> None:
    """Remove a deleted post from followers' Redis feed inboxes.

    Uses pipelined LREM commands to keep Redis round-trips minimal.

    Args:
        post_id: UUID of the deleted post.
        author_id: UUID of the post author.
    """
    try:
        from django_redis import get_redis_connection

        from core.follows.selectors import FollowSelector

        follower_ids = FollowSelector.get_follower_ids(author_id)
        if not follower_ids:
            return

        redis_conn = get_redis_connection("default")

        # Pipeline LREM calls — 1 round-trip for the entire batch.
        for i in range(0, len(follower_ids), _FANOUT_CHUNK_SIZE):
            chunk = follower_ids[i : i + _FANOUT_CHUNK_SIZE]
            pipe = redis_conn.pipeline(transaction=False)
            for fid in chunk:
                pipe.lrem(f"feed:inbox:{fid}", 0, post_id)
            pipe.execute()

        # Also remove from celebrity set if applicable.
        celeb_key = f"feed:celebrity:{author_id}"
        redis_conn.zrem(celeb_key, post_id)

        logger.info(
            "post_removed_from_inboxes",
            extra={"post_id": post_id, "followers_cleaned": len(follower_ids)},
        )

    except Exception:
        logger.warning(
            "post_removal_from_inboxes_failed",
            extra={"post_id": post_id},
            exc_info=True,
        )

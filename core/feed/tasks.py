"""
Feed background tasks — cache invalidation and feed pre-generation.

These tasks run asynchronously via Celery to keep feed caches fresh
after content changes.
"""

import logging

from celery import shared_task

logger = logging.getLogger("core.feed")


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

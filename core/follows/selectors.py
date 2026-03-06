"""
Follow selectors — optimized read queries for the social graph.

Provides cached lookups for follow status and follower/following ID lists.
"""

import logging

from core.follows.models import Follow

logger = logging.getLogger("core.follows")


class FollowSelector:
    """Optimized read queries for follow relationships."""

    @staticmethod
    def is_following(follower_id: str, following_id: str) -> bool:
        """Check if one user follows another.

        Uses cache when available.

        Args:
            follower_id: UUID of the potential follower.
            following_id: UUID of the potentially followed user.

        Returns:
            True if follower_id follows following_id.
        """
        cache_key = f"is_following:{follower_id}:{following_id}"

        try:
            from django.core.cache import cache

            cached = cache.get(cache_key)
            if cached is not None:
                return cached
        except Exception:  # noqa: S110
            pass

        result = Follow.objects.filter(
            follower_id=follower_id,
            following_id=following_id,
        ).exists()

        try:
            from django.core.cache import cache

            cache.set(cache_key, result, timeout=300)  # 5 min TTL
        except Exception:  # noqa: S110
            pass

        return result

    @staticmethod
    def get_follower_ids(user_id: str) -> list[str]:
        """Get all follower IDs for a user.

        Uses cache when available.

        Args:
            user_id: UUID of the user.

        Returns:
            List of follower user IDs.
        """
        cache_key = f"followers:{user_id}"

        try:
            from django.core.cache import cache

            cached = cache.get(cache_key)
            if cached is not None:
                return cached
        except Exception:  # noqa: S110
            pass

        ids = [
            str(uid)
            for uid in Follow.objects.filter(following_id=user_id).values_list(
                "follower_id", flat=True
            )
        ]

        try:
            from django.core.cache import cache

            cache.set(cache_key, ids, timeout=300)
        except Exception:  # noqa: S110
            pass

        return ids

    @staticmethod
    def get_following_ids(user_id: str) -> list[str]:
        """Get all user IDs that a user follows.

        Uses cache when available.

        Args:
            user_id: UUID of the user.

        Returns:
            List of followed user IDs.
        """
        cache_key = f"following:{user_id}"

        try:
            from django.core.cache import cache

            cached = cache.get(cache_key)
            if cached is not None:
                return cached
        except Exception:  # noqa: S110
            pass

        ids = [
            str(uid)
            for uid in Follow.objects.filter(follower_id=user_id).values_list(
                "following_id", flat=True
            )
        ]

        try:
            from django.core.cache import cache

            cache.set(cache_key, ids, timeout=300)
        except Exception:  # noqa: S110
            pass

        return ids

    @staticmethod
    def get_follower_count(user_id: str) -> int:
        """Get the number of followers for a user.

        Args:
            user_id: UUID of the user.

        Returns:
            Number of followers.
        """
        return Follow.objects.filter(following_id=user_id).count()

    @staticmethod
    def get_following_count(user_id: str) -> int:
        """Get the number of users a user follows.

        Args:
            user_id: UUID of the user.

        Returns:
            Number of users being followed.
        """
        return Follow.objects.filter(follower_id=user_id).count()

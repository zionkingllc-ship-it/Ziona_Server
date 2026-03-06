"""
Engagement selectors — optimized read queries for engagement data.

Provides batch-optimized methods for checking engagement status
and fetching aggregated counts.
"""

import logging

from django.db.models import Count, Q

from core.engagement.models import CommentLike, Like, Save

logger = logging.getLogger("core.engagement")


class EngagementSelector:
    """Optimized read queries for engagement data."""

    @staticmethod
    def get_engagement_counts(post_ids: list[str]) -> dict[str, dict]:
        """Get engagement counts for multiple posts in a single query.

        Args:
            post_ids: List of post UUIDs.

        Returns:
            Dict mapping post_id -> {likes, comments, saves, shares}.
        """
        from core.posts.models import Post

        posts = (
            Post.objects.filter(id__in=post_ids, deleted_at__isnull=True)
            .annotate(
                likes_count=Count("likes", distinct=True),
                comments_count=Count(
                    "comments",
                    filter=Q(comments__deleted_at__isnull=True),
                    distinct=True,
                ),
                shares_count=Count("shares", distinct=True),
                saves_count=Count("saves", distinct=True),
            )
            .values(
                "id",
                "likes_count",
                "comments_count",
                "shares_count",
                "saves_count",
            )
        )

        return {
            str(p["id"]): {
                "likes": p["likes_count"],
                "comments": p["comments_count"],
                "shares": p["shares_count"],
                "saves": p["saves_count"],
            }
            for p in posts
        }

    @staticmethod
    def get_user_engagement_status(
        user_id: str,
        post_ids: list[str],
    ) -> dict[str, dict]:
        """Check a user's engagement status for multiple posts.

        Args:
            user_id: UUID of the user.
            post_ids: List of post UUIDs.

        Returns:
            Dict mapping post_id -> {liked: bool, saved: bool}.
        """
        liked_post_ids = set(
            Like.objects.filter(user_id=user_id, post_id__in=post_ids).values_list(
                "post_id", flat=True
            )
        )

        saved_post_ids = set(
            Save.objects.filter(user_id=user_id, post_id__in=post_ids).values_list(
                "post_id", flat=True
            )
        )

        return {
            pid: {
                "liked": pid in liked_post_ids,
                "saved": pid in saved_post_ids,
            }
            for pid in post_ids
        }

    @staticmethod
    def has_user_liked(user_id: str, post_id: str) -> bool:
        """Check if a user has liked a specific post.

        Args:
            user_id: UUID of the user.
            post_id: UUID of the post.

        Returns:
            True if the user has liked the post.
        """
        return Like.objects.filter(user_id=user_id, post_id=post_id).exists()

    @staticmethod
    def has_user_saved(user_id: str, post_id: str) -> bool:
        """Check if a user has saved a specific post.

        Args:
            user_id: UUID of the user.
            post_id: UUID of the post.

        Returns:
            True if the user has saved the post.
        """
        return Save.objects.filter(user_id=user_id, post_id=post_id).exists()

    @staticmethod
    def has_user_liked_comment(user_id: str, comment_id: str) -> bool:
        """Check if a user has liked a specific comment.

        Args:
            user_id: UUID of the user.
            comment_id: UUID of the comment.

        Returns:
            True if the user has liked the comment.
        """
        return CommentLike.objects.filter(user_id=user_id, comment_id=comment_id).exists()

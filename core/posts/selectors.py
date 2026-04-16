"""
Post selectors — optimized queries for the posts domain.

All selectors use select_related and prefetch_related
to prevent N+1 queries.
"""

import logging

from django.db.models import Count, Q, QuerySet

from core.posts.models import Post

logger = logging.getLogger("core.posts")


class PostSelector:
    """Optimized read queries for posts."""

    @staticmethod
    def get_post_with_context(post_id: str) -> Post | None:
        """Fetch a single post with all related data.

        Uses select_related for the author and prefetch_related
        for media items to minimize database round-trips.

        Args:
            post_id: UUID of the post.

        Returns:
            Post instance with preloaded relations, or None.
        """
        return (
            Post.objects.select_related("user")
            # Prefetch both relations: media_files (M2M, used by create_post)
            # and post_media (legacy FK, kept for backward-compat read paths).
            .prefetch_related("media_files", "post_media")
            .filter(id=post_id, deleted_at__isnull=True)
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
            .first()
        )

    @staticmethod
    def get_feed_posts(post_ids: list[str]) -> QuerySet[Post]:
        """Fetch multiple posts optimized for feed rendering.

        Loads author, media, and annotates engagement counts
        in a single query pass.

        Args:
            post_ids: List of post UUIDs to fetch.

        Returns:
            QuerySet of Post instances with annotations.
        """
        return (
            Post.objects.select_related("user")
            # media_files (M2M) is the active relation; post_media is legacy.
            .prefetch_related("media_files", "post_media")
            .filter(id__in=post_ids, deleted_at__isnull=True)
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
        )

    @staticmethod
    def get_user_posts(
        user_id: str,
        cursor: str | None = None,
        limit: int = 20,
    ) -> QuerySet[Post]:
        """Fetch a user's posts with cursor pagination.

        Args:
            user_id: UUID of the post author.
            cursor: Post ID to paginate after.
            limit: Maximum posts to return.

        Returns:
            QuerySet of the user's posts.
        """
        qs = (
            Post.objects.select_related("user")
            # media_files (M2M) is the active relation; post_media is legacy.
            .prefetch_related("media_files", "post_media")
            .filter(user_id=user_id, deleted_at__isnull=True)
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
            # -id tiebreaker ensures deterministic keyset pagination when
            # multiple posts share the same created_at timestamp.
            .order_by("-created_at", "-id")
        )

        if cursor:
            try:
                cursor_post = Post.objects.filter(id=cursor).values("created_at", "id").first()
                if cursor_post:
                    qs = qs.filter(
                        Q(created_at__lt=cursor_post["created_at"])
                        | Q(
                            created_at=cursor_post["created_at"],
                            id__lt=cursor_post["id"],
                        )
                    )
            except Exception:  # noqa: S110
                pass

        return qs[:limit]

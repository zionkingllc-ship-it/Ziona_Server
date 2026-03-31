"""
Feed service — business logic for generating personalized content feeds.

Implements For You (new vs returning user), Following (chronological),
and Discover (category-based) feed algorithms.
"""

import logging

from django.db.models import Count, F, Q
from django.utils import timezone

from core.follows.selectors import FollowSelector
from core.posts.models import Post
from core.shared.dtos import (
    EmptyStateDTO,
    FeedResponseDTO,
    UserSuggestionDTO,
)

logger = logging.getLogger("core.feed")

NEW_USER_THRESHOLD_DAYS = 7
DEFAULT_FEED_LIMIT = 20
FEED_CACHE_TTL = 300


class FeedService:
    """Service handling feed generation and caching."""

    @staticmethod
    def get_feed(
        viewer_id: str | None = None,
        cursor: str | None = None,
        limit: int = DEFAULT_FEED_LIMIT,
    ) -> FeedResponseDTO:
        """Get public or personalized feed.

        Args:
            viewer_id: UUID of the requesting user (optional).
            cursor: Post ID for cursor pagination.
            limit: Page size.

        Returns:
            FeedResponseDTO.
        """
        if viewer_id:
            # For now, map to for-you feed which handles ranking
            return FeedService.get_for_you_feed(user_id=viewer_id, cursor=cursor, limit=limit)

        # Unauthenticated: Show popular content
        return FeedService._public_discovery_feed(cursor, limit)

    @staticmethod
    def get_for_you_feed(
        user_id: str,
        cursor: str | None = None,
        limit: int = DEFAULT_FEED_LIMIT,
    ) -> FeedResponseDTO:
        """Generate the For You feed.

        New users (<7 days) see popular content.
        Returning users see a ranked mix of followed + discovery content.

        Args:
            user_id: UUID of the requesting user.
            cursor: Post ID for cursor pagination.
            limit: Page size.

        Returns:
            FeedResponseDTO with posts, pagination, and empty state.
        """
        from core.users.models import User

        limit = min(limit, 50)

        user = User.objects.filter(id=user_id).first()
        if not user:
            return FeedResponseDTO(posts=[], has_more=False)

        is_new_user = (timezone.now() - user.created_at).days < NEW_USER_THRESHOLD_DAYS

        logger.info(f"Total posts in DB: {Post.objects.count()}")
        logger.info(f"Active posts: {Post.objects.filter(deleted_at__isnull=True).count()}")
        logger.info(f"Posts with category: {Post.objects.filter(category__isnull=False).count()}")

        if is_new_user:
            result = FeedService._new_user_feed(user_id, cursor, limit)
        else:
            result = FeedService._returning_user_feed(user_id, cursor, limit)

        logger.info(f"Posts after filtering: {len(result.posts)}")
        logger.info(f"First 3 posts: {[p.id for p in result.posts[:3]]}")

        return result

    @staticmethod
    def get_following_feed(
        user_id: str,
        cursor: str | None = None,
        limit: int = DEFAULT_FEED_LIMIT,
    ) -> FeedResponseDTO:
        """Generate the Following feed — chronological posts from followed users.

        Args:
            user_id: UUID of the requesting user.
            cursor: Post ID for cursor pagination.
            limit: Page size.

        Returns:
            FeedResponseDTO with posts and empty state suggestions.
        """
        limit = min(limit, 50)

        following_ids = FollowSelector.get_following_ids(user_id)

        if not following_ids:
            suggestions = FeedService._get_empty_state_suggestions(user_id)
            return FeedResponseDTO(
                posts=[],
                has_more=False,
                empty_state=EmptyStateDTO(
                    message="Follow creators to see their posts here!",
                    suggestions=suggestions,
                ),
            )

        qs = (
            Post.objects.select_related("user")
            .prefetch_related("media_files", "post_media")
            .filter(
                user_id__in=following_ids,
                deleted_at__isnull=True,
            )
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
            .order_by("-created_at")
        )

        if cursor:
            qs = FeedService._apply_cursor(qs, cursor)

        posts = list(qs[: limit + 1])
        has_more = len(posts) > limit
        posts = posts[:limit]

        post_dtos = FeedService._bulk_build_post_dtos(posts, user_id)

        return FeedResponseDTO(
            posts=post_dtos,
            next_cursor=str(posts[-1].id) if has_more and posts else None,
            has_more=has_more,
        )

    @staticmethod
    def get_discover_feed(
        user_id: str | None = None,
        category: str | None = None,
        cursor: str | None = None,
        limit: int = DEFAULT_FEED_LIMIT,
    ) -> FeedResponseDTO:
        """Generate the Discover feed — category-based content.

        Args:
            user_id: UUID of the requesting user.
            category: Optional PostCategory filter.
            cursor: Post ID for cursor pagination.
            limit: Page size.

        Returns:
            FeedResponseDTO with posts.
        """
        limit = min(limit, 50)

        qs = (
            Post.objects.select_related("user")
            .prefetch_related("media_files", "post_media")
            .filter(deleted_at__isnull=True)
            # .exclude(user_id=user_id)  # Temporarily disabled per user request
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

        if category:
            qs = qs.filter(category__slug=category)

        qs = qs.order_by("-created_at")

        if cursor:
            qs = FeedService._apply_cursor(qs, cursor)

        posts = list(qs[: limit + 1])
        has_more = len(posts) > limit
        posts = posts[:limit]

        if not posts:
            suggestions = FeedService._get_empty_state_suggestions(user_id) if user_id else []
            return FeedResponseDTO(
                posts=[],
                has_more=False,
                empty_state=EmptyStateDTO(
                    message="Check back later for new discovery content!",
                    suggestions=suggestions,
                ),
            )

        post_dtos = FeedService._bulk_build_post_dtos(posts, user_id)

        return FeedResponseDTO(
            posts=post_dtos,
            next_cursor=str(posts[-1].id) if has_more and posts else None,
            has_more=has_more,
        )

    @staticmethod
    def _new_user_feed(
        user_id: str,
        cursor: str | None,
        limit: int,
    ) -> FeedResponseDTO:
        """Feed for new users (<7 days) — popular content."""
        from core.users.models import UserInterest

        user_interests = list(
            UserInterest.objects.filter(user_id=user_id).values_list("interest", flat=True)
        )

        qs = (
            Post.objects.select_related("user")
            .prefetch_related("media_files", "post_media")
            .filter(deleted_at__isnull=True)
            # .exclude(user_id=user_id)  # Temporarily disabled per user request
            .annotate(
                likes_count=Count("likes", distinct=True),
                comments_count=Count(
                    "comments",
                    filter=Q(comments__deleted_at__isnull=True),
                    distinct=True,
                ),
                shares_count=Count("shares", distinct=True),
                saves_count=Count("saves", distinct=True),
                engagement_score=F("likes_count") + F("comments_count") * 2 + F("shares_count") * 3,
            )
        )

        if user_interests:
            qs = qs.filter(Q(category__in=user_interests) | Q(category__isnull=True))

        qs = qs.order_by("-engagement_score", "-created_at")

        if cursor:
            qs = FeedService._apply_cursor(qs, cursor)

        posts = list(qs[: limit + 1])
        has_more = len(posts) > limit
        posts = posts[:limit]

        if not posts:
            suggestions = FeedService._get_empty_state_suggestions(user_id)
            return FeedResponseDTO(
                posts=[],
                has_more=False,
                empty_state=EmptyStateDTO(
                    message="Welcome to Ziona! Explore and follow creators.",
                    suggestions=suggestions,
                ),
            )

        post_dtos = FeedService._bulk_build_post_dtos(posts, user_id)

        return FeedResponseDTO(
            posts=post_dtos,
            next_cursor=str(posts[-1].id) if has_more and posts else None,
            has_more=has_more,
        )

    @staticmethod
    def _returning_user_feed(
        user_id: str,
        cursor: str | None,
        limit: int,
    ) -> FeedResponseDTO:
        """Feed for returning users — mix of followed + discovery."""
        following_ids = FollowSelector.get_following_ids(user_id)

        qs = (
            Post.objects.select_related("user")
            .prefetch_related("media_files", "post_media")
            .filter(deleted_at__isnull=True)
            # .exclude(user_id=user_id)  # Temporarily disabled per user request
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

        if following_ids:
            from django.db.models import Case, IntegerField, Value, When

            qs = qs.annotate(
                is_following=Case(
                    When(user_id__in=following_ids, then=Value(1)),
                    default=Value(0),
                    output_field=IntegerField(),
                ),
            ).order_by("-is_following", "-created_at")
        else:
            qs = qs.order_by("-created_at")

        if cursor:
            qs = FeedService._apply_cursor(qs, cursor)

        posts = list(qs[: limit + 1])
        has_more = len(posts) > limit
        posts = posts[:limit]

        if not posts:
            suggestions = FeedService._get_empty_state_suggestions(user_id)
            return FeedResponseDTO(
                posts=[],
                has_more=False,
                empty_state=EmptyStateDTO(
                    message="No posts yet. Follow creators to fill your feed!",
                    suggestions=suggestions,
                ),
            )

        post_dtos = FeedService._bulk_build_post_dtos(posts, user_id)

        return FeedResponseDTO(
            posts=post_dtos,
            next_cursor=str(posts[-1].id) if has_more and posts else None,
            has_more=has_more,
        )

    @staticmethod
    def _public_discovery_feed(
        cursor: str | None,
        limit: int,
    ) -> FeedResponseDTO:
        """Feed for unauthenticated users — popular content."""
        qs = (
            Post.objects.select_related("user")
            .prefetch_related("media_files", "post_media")
            .filter(deleted_at__isnull=True)
            .annotate(
                likes_count=Count("likes", distinct=True),
                comments_count=Count(
                    "comments",
                    filter=Q(comments__deleted_at__isnull=True),
                    distinct=True,
                ),
                shares_count=Count("shares", distinct=True),
                saves_count=Count("saves", distinct=True),
                engagement_score=F("likes_count") + F("comments_count") * 2 + F("shares_count") * 3,
            )
            .order_by("-engagement_score", "-created_at")
        )

        if cursor:
            qs = FeedService._apply_cursor(qs, cursor)

        posts = list(qs[: limit + 1])
        has_more = len(posts) > limit
        posts = posts[:limit]

        post_dtos = FeedService._bulk_build_post_dtos(posts, viewer_id=None)

        return FeedResponseDTO(
            posts=post_dtos,
            next_cursor=str(posts[-1].id) if has_more and posts else None,
            has_more=has_more,
        )

    @staticmethod
    def _apply_cursor(qs, cursor: str):
        """Apply cursor-based pagination to a queryset."""
        try:
            cursor_post = Post.objects.filter(id=cursor).values("created_at").first()
            if cursor_post:
                qs = qs.filter(created_at__lt=cursor_post["created_at"])
        except Exception:  # noqa: S110
            pass
        return qs

    @staticmethod
    def _get_empty_state_suggestions(
        user_id: str,
        limit: int = 5,
    ) -> list[UserSuggestionDTO]:
        """Get user suggestions for empty feed states."""
        from core.follows.services import FollowService

        suggestions_data = FollowService.get_suggested_creators(user_id, limit=limit)

        return [
            UserSuggestionDTO(
                id=s["user"].id,
                username=s["user"].username,
                avatar_url=s["user"].avatar_url,
                bio=s.get("bio"),
                followers_count=s.get("followers_count", 0),
            )
            for s in suggestions_data
        ]

    @staticmethod
    def _bulk_build_post_dtos(
        posts: list,
        viewer_id: str | None = None,
    ) -> list:
        """Build PostResponseDTOs for a list of posts with bulk viewer state fetching.

        Instead of 3 queries per post (liked/saved/following), this method
        fetches all viewer state data in just 3 total queries.

        Args:
            posts: List of Post instances (annotated with counts).
            viewer_id: Optional viewer user ID.

        Returns:
            List of PostResponseDTO instances.
        """
        from core.posts.services import PostService

        if not posts:
            return []

        post_ids = [str(p.id) for p in posts]
        author_ids = list({str(p.user_id) for p in posts})

        liked_post_ids: set = set()
        saved_post_ids: set = set()
        following_user_ids: set = set()

        if viewer_id:
            from core.engagement.models import Like, Save
            from core.follows.models import Follow

            liked_post_ids = set(
                Like.objects.filter(user_id=viewer_id, post_id__in=post_ids).values_list(
                    "post_id", flat=True
                )
            )
            # Convert UUIDs to strings for set lookup
            liked_post_ids = {str(pid) for pid in liked_post_ids}

            saved_post_ids = set(
                Save.objects.filter(user_id=viewer_id, post_id__in=post_ids).values_list(
                    "post_id", flat=True
                )
            )
            saved_post_ids = {str(pid) for pid in saved_post_ids}

            following_user_ids = set(
                Follow.objects.filter(
                    follower_id=viewer_id, following_id__in=author_ids
                ).values_list("following_id", flat=True)
            )
            following_user_ids = {str(uid) for uid in following_user_ids}

        return [
            PostService._build_post_dto(
                post=p,
                media_items=list(p.media_files.all()),
                viewer_id=viewer_id,
                liked_post_ids=liked_post_ids,
                saved_post_ids=saved_post_ids,
                following_user_ids=following_user_ids,
            )
            for p in posts
        ]

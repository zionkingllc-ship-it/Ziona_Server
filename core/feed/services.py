"""
Feed service — business logic for generating personalized content feeds.

Implements For You (new vs returning user), Following (chronological),
and Discover (category-based) feed algorithms.
"""

import logging

from django.db.models import Count, F, Q
from django.utils import timezone

from core.follows.selectors import FollowSelector
from core.posts.models import Post, PostCategory
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

        if is_new_user:
            return FeedService._new_user_feed(user_id, cursor, limit)
        return FeedService._returning_user_feed(user_id, cursor, limit)

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

        from core.posts.services import PostService

        post_dtos = [
            PostService._build_post_dto(
                post=p,
                media_items=list(p.media_files.all()) or list(p.post_media.all()),
                viewer_id=user_id,
            )
            for p in posts
        ]

        return FeedResponseDTO(
            posts=post_dtos,
            next_cursor=str(posts[-1].id) if has_more and posts else None,
            has_more=has_more,
        )

    @staticmethod
    def get_discover_feed(
        user_id: str,
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
            .exclude(user_id=user_id)
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
            valid_categories = [c.value for c in PostCategory]
            if category in valid_categories:
                qs = qs.filter(category=category)

        qs = qs.order_by("-created_at")

        if cursor:
            qs = FeedService._apply_cursor(qs, cursor)

        posts = list(qs[: limit + 1])
        has_more = len(posts) > limit
        posts = posts[:limit]

        from core.posts.services import PostService

        post_dtos = [
            PostService._build_post_dto(
                post=p,
                media_items=list(p.media_files.all()) or list(p.post_media.all()),
                viewer_id=user_id,
            )
            for p in posts
        ]

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
            .prefetch_related("post_media")
            .filter(deleted_at__isnull=True)
            .exclude(user_id=user_id)
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

        from core.posts.services import PostService

        post_dtos = [
            PostService._build_post_dto(
                post=p,
                media_items=list(p.post_media.all()),
                viewer_id=user_id,
            )
            for p in posts
        ]

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
            .prefetch_related("post_media")
            .filter(deleted_at__isnull=True)
            .exclude(user_id=user_id)
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

        from core.posts.services import PostService

        post_dtos = [
            PostService._build_post_dto(
                post=p,
                media_items=list(p.post_media.all()),
                viewer_id=user_id,
            )
            for p in posts
        ]

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
            .prefetch_related("media_files")
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

        from core.posts.services import PostService

        post_dtos = [
            PostService._build_post_dto(
                post=p,
                media_items=list(p.media_files.all()) or list(p.post_media.all()),
            )
            for p in posts
        ]

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

"""Feed services package — public FeedService facade.

Split from the former core/feed/services.py (no behavior change).
"""

import logging

from django.db.models import Q
from django.utils import timezone

from core.follows.selectors import FollowSelector
from core.shared.dtos import (
    EmptyStateDTO,
    FeedResponseDTO,
)

logger = logging.getLogger("core.feed")

from core.feed.services import builders, cursors, ranking, strategies  # noqa: E402
from core.feed.services.cursors import FeedCursor  # noqa: E402,F401
from core.feed.services.ranking import (  # noqa: E402,F401
    DEFAULT_FEED_LIMIT,
    FEED_CACHE_TTL,
    NEW_USER_THRESHOLD_DAYS,
    REPORT_SUPPRESSION_THRESHOLD,
)


class FeedService:
    """Feed generation service (implementations in sibling modules)."""

    @staticmethod
    def get_feed(
        viewer_id: str | None = None,
        cursor: str | None = None,
        limit: int = DEFAULT_FEED_LIMIT,
    ) -> FeedResponseDTO:
        """Get public or personalized feed.

        Args:
            viewer_id: UUID of the requesting user (optional).
            cursor: Opaque cursor string for pagination.
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

        Fast path: tries the pre-built Redis inbox first.
        Fallback: DB-based ranking (new user vs returning user algorithm).

        Args:
            user_id: UUID of the requesting user.
            cursor: Opaque cursor string for pagination.
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
            cursor: Opaque cursor string for pagination.
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

        qs = FeedService._with_engagement_counts(
            FeedService._base_post_queryset().filter(user_id__in=following_ids)
        ).filter(unique_reports_count__lt=REPORT_SUPPRESSION_THRESHOLD)
        qs = FeedService._with_creator_affinity(qs, user_id).order_by(
            "-created_at", "-creator_affinity", "-id"
        )

        qs = FeedService._exclude_hidden_posts(qs, user_id)

        if cursor:
            cursor_data = FeedCursor.decode(cursor)
            qs = FeedService._apply_chronological_affinity_cursor(qs, cursor_data, cursor)

        posts = list(qs[: limit + 1])
        has_more = len(posts) > limit
        posts = posts[:limit]

        post_dtos = FeedService._bulk_build_post_dtos(posts, user_id)

        next_cursor = None
        if has_more and posts:
            next_cursor = FeedCursor.encode(
                post_id=str(posts[-1].id),
                algo="following",
                created_at=posts[-1].created_at,
                affinity=getattr(posts[-1], "creator_affinity", 0),
            )

        return FeedResponseDTO(
            posts=post_dtos,
            next_cursor=next_cursor,
            has_more=has_more,
        )

    @staticmethod
    def get_discover_feed(
        user_id: str | None = None,
        category: str | None = None,
        media_type: str | None = None,
        cursor: str | None = None,
        limit: int = DEFAULT_FEED_LIMIT,
    ) -> FeedResponseDTO:
        """Generate the Discover feed — category-based content.

        Args:
            user_id: UUID of the requesting user.
            category: Optional category slug or label filter.
            media_type: Optional media filter (image, video, text).
            cursor: Opaque cursor string for pagination.
            limit: Page size.

        Returns:
            FeedResponseDTO with posts.
        """
        limit = min(limit, 50)

        qs = FeedService._ranked_queryset()

        category_filter = _normalize_discover_category(category)
        if category_filter:
            qs = qs.filter(
                Q(category__slug__iexact=category_filter)
                | Q(category__label__iexact=category_filter)
            )

        media_filter = _normalize_discover_media_type(media_type)
        if media_filter:
            qs = qs.filter(post_type=media_filter)
        elif media_type and str(media_type).strip():
            qs = qs.none()

        qs = FeedService._exclude_hidden_posts(qs, user_id)

        if cursor:
            cursor_data = FeedCursor.decode(cursor)
            qs = FeedService._apply_ranked_cursor(qs, cursor_data, cursor)

        candidate_limit = max(limit * 3, limit + 10)
        candidates = list(qs[: candidate_limit + 1])
        posts = FeedService._apply_creator_diversity(candidates, limit)
        has_more = len(candidates) > len(posts)

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

        next_cursor = None
        if has_more and posts:
            next_cursor = FeedCursor.encode(
                post_id=str(posts[-1].id),
                algo="discover",
                created_at=posts[-1].created_at,
                score=getattr(posts[-1], "final_score", 0),
            )

        return FeedResponseDTO(
            posts=post_dtos,
            next_cursor=next_cursor,
            has_more=has_more,
        )

    @staticmethod
    def search_discover_content(
        query: str,
        user_id: str | None = None,
        category: str | None = None,
        media_type: str | None = None,
        cursor: str | None = None,
        limit: int = DEFAULT_FEED_LIMIT,
    ) -> FeedResponseDTO:
        """Search Discover posts by text, author, and category without changing feeds."""
        limit = min(limit, 50)
        search_term = (query or "").strip()
        if len(search_term) < 2:
            suggestions = FeedService._get_empty_state_suggestions(user_id) if user_id else []
            return FeedResponseDTO(
                posts=[],
                has_more=False,
                empty_state=EmptyStateDTO(
                    message="No matching content found.", suggestions=suggestions
                ),
            )

        qs = FeedService._ranked_queryset().filter(
            Q(caption__icontains=search_term)
            | Q(user__username__icontains=search_term)
            | Q(user__full_name__icontains=search_term)
            | Q(category__label__icontains=search_term)
            | Q(category__slug__icontains=search_term)
        )

        category_filter = _normalize_discover_category(category)
        if category_filter:
            qs = qs.filter(
                Q(category__slug__iexact=category_filter)
                | Q(category__label__iexact=category_filter)
            )

        media_filter = _normalize_discover_media_type(media_type)
        if media_filter:
            qs = qs.filter(post_type=media_filter)
        elif media_type and str(media_type).strip():
            qs = qs.none()

        qs = FeedService._exclude_hidden_posts(qs, user_id)

        if cursor:
            cursor_data = FeedCursor.decode(cursor)
            qs = FeedService._apply_ranked_cursor(qs, cursor_data, cursor)

        candidate_limit = max(limit * 3, limit + 10)
        candidates = list(qs[: candidate_limit + 1])
        posts = FeedService._apply_creator_diversity(candidates, limit)
        has_more = len(candidates) > len(posts)

        suggestions = []
        if not posts and user_id:
            suggestions = FeedService._get_empty_state_suggestions(user_id)

        next_cursor = None
        if has_more and posts:
            next_cursor = FeedCursor.encode(
                post_id=str(posts[-1].id),
                algo="discover_search",
                created_at=posts[-1].created_at,
                score=getattr(posts[-1], "final_score", 0),
            )

        return FeedResponseDTO(
            posts=FeedService._bulk_build_post_dtos(posts, user_id),
            next_cursor=next_cursor,
            has_more=has_more,
            empty_state=(
                EmptyStateDTO(message="No matching content found.", suggestions=suggestions)
                if not posts
                else None
            ),
        )

    # Implementations split into sibling modules; the class surface is unchanged
    # (profiles/engagement/tests use these as FeedService attributes).
    _exclude_hidden_posts = staticmethod(ranking._exclude_hidden_posts)
    _base_post_queryset = staticmethod(ranking._base_post_queryset)
    _with_engagement_counts = staticmethod(ranking._with_engagement_counts)
    _with_final_score = staticmethod(ranking._with_final_score)
    _ranked_queryset = staticmethod(ranking._ranked_queryset)
    _with_creator_affinity = staticmethod(ranking._with_creator_affinity)
    _ranked_cursor_payload = staticmethod(cursors._ranked_cursor_payload)
    _chronological_cursor_payload = staticmethod(cursors._chronological_cursor_payload)
    _apply_cursor = staticmethod(cursors._apply_cursor)
    _apply_ranked_cursor = staticmethod(cursors._apply_ranked_cursor)
    _apply_chronological_affinity_cursor = staticmethod(
        cursors._apply_chronological_affinity_cursor
    )
    _apply_following_cursor = staticmethod(cursors._apply_following_cursor)
    _get_empty_state_suggestions = staticmethod(builders._get_empty_state_suggestions)
    _bulk_build_post_dtos = staticmethod(builders._bulk_build_post_dtos)
    _new_user_feed = staticmethod(strategies._new_user_feed)
    _returning_user_feed = staticmethod(strategies._returning_user_feed)
    _blend_discovery_and_followed = staticmethod(strategies._blend_discovery_and_followed)
    _apply_creator_diversity = staticmethod(strategies._apply_creator_diversity)
    _public_discovery_feed = staticmethod(strategies._public_discovery_feed)


def _normalize_discover_category(category: str | None) -> str:
    value = (category or "").strip()
    if not value or value.lower() == "all":
        return ""
    return value


def _normalize_discover_media_type(media_type: str | None) -> str:
    value = (media_type or "").strip().lower()
    if not value or value == "all":
        return ""

    aliases = {
        "image": "image",
        "images": "image",
        "photo": "image",
        "photos": "image",
        "video": "video",
        "videos": "video",
        "text": "text",
        "texts": "text",
    }
    return aliases.get(value, "")

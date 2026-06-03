"""
Feed service — business logic for generating personalized content feeds.

Implements For You (new vs returning user), Following (chronological),
and Discover (category-based) feed algorithms.

Architecture (Fan-out-on-Write + DB Fallback)
---------------------------------------------
1. **Fast path (Redis Inbox):**  When a creator posts, a Celery task pushes
   the post ID into every follower's personal Redis list.  On feed request
   the server reads IDs from the list, hydrates them from the DB, and
   returns — zero ranking computation required.

2. **DB fallback:**  If the Redis inbox is empty (new user, cold cache, or
   cache miss) the original DB-based ranking algorithm fires.  This is the
   same algorithm that existed before the Redis layer, so there is **no
   loss of functionality** if Redis is temporarily unavailable.

3. **Celebrity hybrid:**  Accounts with >50 000 followers skip the fan-out
   (too expensive).  Their posts are stored in a per-author Redis sorted
   set and merged at read time — only for followers who actually request
   their feed.

4. **Opaque cursors:**  Cursor tokens now carry the algorithm version and
   tier context as a Base64-encoded JSON blob.  This prevents the "tier
   flip" bug where a follow/unfollow between pages caused massive content
   jumps.  Old raw-UUID cursors from in-flight mobile clients are decoded
   gracefully via a fallback path.
"""

import base64
import json
import logging
from datetime import timedelta

from django.db.models import (
    Case,
    Count,
    Exists,
    ExpressionWrapper,
    F,
    FloatField,
    IntegerField,
    OuterRef,
    Q,
    Subquery,
    Value,
    When,
)
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.dateparse import parse_datetime

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

# Celebrity threshold — must match the value in tasks.py.
CELEBRITY_FOLLOWER_THRESHOLD = 50_000

# Maximum number of IDs to read from a Redis inbox in a single LRANGE.
_INBOX_READ_LIMIT = 60

DISCOVERY_BLEND_SIZE = 3
FOLLOWED_BLEND_SIZE = 1
REPORT_PENALTY_THRESHOLD = 5
REPORT_SUPPRESSION_THRESHOLD = 10
CREATOR_DIVERSITY_WINDOW = 10
CREATOR_DIVERSITY_MAX_PER_WINDOW = 2


# =========================================================================
# Opaque Cursor (Phase 2)
# =========================================================================


class FeedCursor:
    """Opaque cursor that encodes feed pagination state as Base64 JSON.

    Format::

        base64({"v":1, "id":"<post_uuid>", "algo":"<algo_tag>",
                "ts":"<iso8601>", "tier":<int|null>})

    Backward-compatible: if decoding fails (e.g. the value is a raw UUID
    from an older mobile client) the cursor is treated as a legacy value
    and the ``algo`` / ``tier`` fields default to ``None``.
    """

    VERSION = 2

    @staticmethod
    def encode(
        post_id: str,
        algo: str,
        created_at,
        *,
        tier: int | None = None,
        score: float | None = None,
        **extra,
    ) -> str:
        """Encode pagination state into an opaque cursor string."""
        payload = {
            "v": FeedCursor.VERSION,
            "id": str(post_id),
            "algo": algo,
            "ts": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at),
        }
        if tier is not None:
            payload["tier"] = tier
        if score is not None:
            payload["score"] = float(score)
        if extra:
            payload.update(extra)
        return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()

    @staticmethod
    def decode(cursor: str) -> dict:
        """Decode an opaque cursor, falling back to legacy UUID format."""
        if not cursor:
            return {}
        try:
            raw = base64.urlsafe_b64decode(cursor.encode()).decode()
            data = json.loads(raw)
            if data.get("v") in (1, FeedCursor.VERSION):
                return data
        except Exception:
            logger.debug("invalid cursor format — falling back to legacy uuid")
        # Backward-compatible: treat as raw post UUID from older clients.
        return {"id": cursor, "algo": None, "ts": None, "tier": None}


# =========================================================================
# Feed Service
# =========================================================================


class FeedService:
    """Service handling feed generation and caching."""

    @staticmethod
    def _exclude_hidden_posts(qs, user_id: str | None):
        """Exclude posts hidden by the user using a performant NOT EXISTS subquery."""
        if not user_id:
            return qs

        from core.engagement.models import HiddenPost

        hidden_subquery = Exists(HiddenPost.objects.filter(user_id=user_id, post_id=OuterRef("pk")))
        return qs.annotate(is_hidden=hidden_subquery).filter(is_hidden=False)

    @staticmethod
    def _base_post_queryset():
        """Common feed queryset with related objects needed for feed DTO hydration."""
        return (
            Post.objects.select_related("user")
            .prefetch_related("media_files", "post_media")
            .filter(deleted_at__isnull=True)
        )

    @staticmethod
    def _with_engagement_counts(qs):
        """Annotate reusable engagement counts for feed ranking and DTO stats."""
        return qs.annotate(
            likes_count=Count("likes", distinct=True),
            comments_count=Count(
                "comments",
                filter=Q(comments__deleted_at__isnull=True),
                distinct=True,
            ),
            shares_count=Count("shares", distinct=True),
            saves_count=Count("saves", distinct=True),
            unique_reports_count=Count("reports__reporter_id", distinct=True),
        )

    @staticmethod
    def _with_final_score(qs):
        """Annotate MVP ranking score: engagement × freshness × report penalty."""
        now = timezone.now()
        return FeedService._with_engagement_counts(qs).annotate(
            engagement_score=ExpressionWrapper(
                F("likes_count")
                + (F("comments_count") * Value(2))
                + (F("shares_count") * Value(3)),
                output_field=FloatField(),
            ),
            freshness_multiplier=Case(
                When(created_at__gte=now - timedelta(days=1), then=Value(1.0)),
                When(created_at__gte=now - timedelta(days=3), then=Value(0.7)),
                When(created_at__gte=now - timedelta(days=7), then=Value(0.4)),
                default=Value(0.1),
                output_field=FloatField(),
            ),
            report_penalty_multiplier=Case(
                When(unique_reports_count__gte=REPORT_PENALTY_THRESHOLD, then=Value(0.5)),
                default=Value(1.0),
                output_field=FloatField(),
            ),
            final_score=ExpressionWrapper(
                F("engagement_score") * F("freshness_multiplier") * F("report_penalty_multiplier"),
                output_field=FloatField(),
            ),
        )

    @staticmethod
    def _ranked_queryset():
        """Base algorithmic feed queryset ordered by score, freshness, and stable ID tie-breaker."""
        return (
            FeedService._with_final_score(FeedService._base_post_queryset())
            .filter(unique_reports_count__lt=REPORT_SUPPRESSION_THRESHOLD)
            .order_by("-final_score", "-created_at", "-id")
        )

    @staticmethod
    def _with_creator_affinity(qs, user_id: str):
        """Annotate likes this viewer gave each creator in the last 30 days."""
        from core.engagement.models import Like

        cutoff = timezone.now() - timedelta(days=30)
        affinity = (
            Like.objects.filter(
                user_id=user_id,
                post__user_id=OuterRef("user_id"),
                created_at__gte=cutoff,
            )
            .values("post__user_id")
            .annotate(total=Count("id"))
            .values("total")[:1]
        )
        return qs.annotate(
            creator_affinity=Coalesce(
                Subquery(affinity, output_field=IntegerField()),
                Value(0),
            )
        )

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
        cursor: str | None = None,
        limit: int = DEFAULT_FEED_LIMIT,
    ) -> FeedResponseDTO:
        """Generate the Discover feed — category-based content.

        Args:
            user_id: UUID of the requesting user.
            category: Optional PostCategory filter.
            cursor: Opaque cursor string for pagination.
            limit: Page size.

        Returns:
            FeedResponseDTO with posts.
        """
        limit = min(limit, 50)

        qs = FeedService._ranked_queryset()

        if category:
            qs = qs.filter(category__slug=category)

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

        qs = FeedService._ranked_queryset()

        if user_interests:
            # Filter by category slug, not by the Category FK (UUID).
            # `user_interests` stores slug strings (e.g. "love", "trust"), so
            # category__in would compare UUIDs against strings and never match.
            qs = qs.filter(Q(category__slug__in=user_interests) | Q(category__isnull=True))

        qs = FeedService._exclude_hidden_posts(qs, user_id)

        if cursor:
            cursor_data = FeedCursor.decode(cursor)
            qs = FeedService._apply_ranked_cursor(qs, cursor_data, cursor)

        candidate_limit = max(limit * 3, limit + 10)
        candidates = list(qs[: candidate_limit + 1])
        posts = FeedService._apply_creator_diversity(candidates, limit)
        has_more = len(candidates) > len(posts)

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

        next_cursor = None
        if has_more and posts:
            next_cursor = FeedCursor.encode(
                post_id=str(posts[-1].id),
                algo="new",
                created_at=posts[-1].created_at,
                score=getattr(posts[-1], "final_score", 0),
            )

        return FeedResponseDTO(
            posts=post_dtos,
            next_cursor=next_cursor,
            has_more=has_more,
        )

    @staticmethod
    def _returning_user_feed(
        user_id: str,
        cursor: str | None,
        limit: int,
    ) -> FeedResponseDTO:
        """Feed for returning users — discovery-first 3:1 blend."""
        following_ids = FollowSelector.get_following_ids(user_id)
        following_ids_set = {str(fid) for fid in following_ids}
        cursor_data = FeedCursor.decode(cursor) if cursor else {}
        start_slot = int(cursor_data.get("slot", 0) or 0)
        candidate_limit = max(limit * 5, 40)

        discovery_qs = FeedService._ranked_queryset()
        if following_ids_set:
            discovery_qs = discovery_qs.exclude(user_id__in=following_ids_set)
        discovery_qs = FeedService._exclude_hidden_posts(discovery_qs, user_id)

        legacy_cursor = (
            cursor_data if cursor_data.get("id") and not cursor_data.get("discovery") else {}
        )
        discovery_cursor = cursor_data.get("discovery") or legacy_cursor
        if discovery_cursor:
            discovery_qs = FeedService._apply_ranked_cursor(discovery_qs, discovery_cursor)

        followed_qs = FeedService._with_engagement_counts(
            FeedService._base_post_queryset().filter(user_id__in=following_ids)
        ).filter(unique_reports_count__lt=REPORT_SUPPRESSION_THRESHOLD)
        followed_qs = FeedService._exclude_hidden_posts(followed_qs, user_id).order_by(
            "-created_at", "-id"
        )

        followed_cursor = cursor_data.get("followed") or legacy_cursor
        if followed_cursor:
            followed_qs = FeedService._apply_cursor(followed_qs, followed_cursor.get("id"))

        discovery_candidates = list(discovery_qs[: candidate_limit + 1])
        followed_candidates = list(followed_qs[: candidate_limit + 1]) if following_ids else []

        blended = FeedService._blend_discovery_and_followed(
            discovery_candidates,
            followed_candidates,
            target_count=max(limit * 3, limit + 10),
            start_slot=start_slot,
        )
        posts = FeedService._apply_creator_diversity(blended, limit)

        has_more = (
            len(discovery_candidates) > candidate_limit
            or len(followed_candidates) > candidate_limit
            or len(blended) > len(posts)
        )

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

        selected_discovery = [post for post in posts if str(post.user_id) not in following_ids_set]
        selected_followed = [post for post in posts if str(post.user_id) in following_ids_set]
        last_discovery = selected_discovery[-1] if selected_discovery else None
        last_followed = selected_followed[-1] if selected_followed else None
        last_post = posts[-1]

        next_cursor = None
        if has_more:
            next_cursor = FeedCursor.encode(
                post_id=str(last_post.id),
                algo="returning",
                created_at=last_post.created_at,
                score=getattr(last_post, "final_score", None),
                slot=(start_slot + len(posts)) % (DISCOVERY_BLEND_SIZE + FOLLOWED_BLEND_SIZE),
                discovery=FeedService._ranked_cursor_payload(last_discovery, discovery_cursor),
                followed=FeedService._chronological_cursor_payload(last_followed, followed_cursor),
            )

        return FeedResponseDTO(
            posts=post_dtos,
            next_cursor=next_cursor,
            has_more=has_more,
        )

    @staticmethod
    def _blend_discovery_and_followed(
        discovery_posts: list,
        followed_posts: list,
        *,
        target_count: int,
        start_slot: int = 0,
    ) -> list:
        """Blend ranked discovery with followed posts using a 3:1 slot pattern."""
        result = []
        seen = set()
        discovery_index = 0
        followed_index = 0
        slot = start_slot

        while len(result) < target_count and (
            discovery_index < len(discovery_posts) or followed_index < len(followed_posts)
        ):
            wants_followed = (
                slot % (DISCOVERY_BLEND_SIZE + FOLLOWED_BLEND_SIZE) >= DISCOVERY_BLEND_SIZE
            )
            pools = (
                (
                    (followed_posts, "followed"),
                    (discovery_posts, "discovery"),
                )
                if wants_followed
                else (
                    (discovery_posts, "discovery"),
                    (followed_posts, "followed"),
                )
            )

            added = False
            for pool, pool_name in pools:
                index = followed_index if pool_name == "followed" else discovery_index
                while index < len(pool) and str(pool[index].id) in seen:
                    index += 1
                if index >= len(pool):
                    if pool_name == "followed":
                        followed_index = index
                    else:
                        discovery_index = index
                    continue

                post = pool[index]
                result.append(post)
                seen.add(str(post.id))
                added = True
                if pool_name == "followed":
                    followed_index = index + 1
                else:
                    discovery_index = index + 1
                break

            if not added:
                break
            slot += 1

        return result

    @staticmethod
    def _apply_creator_diversity(posts: list, limit: int) -> list:
        """Prefer no consecutive creators and max two per creator per 10-post window."""
        selected = []
        skipped = []

        for post in posts:
            author_id = str(post.user_id)
            recent_window = selected[-(CREATOR_DIVERSITY_WINDOW - 1) :]
            author_window_count = sum(1 for item in recent_window if str(item.user_id) == author_id)
            same_as_previous = bool(selected and str(selected[-1].user_id) == author_id)

            if same_as_previous or author_window_count >= CREATOR_DIVERSITY_MAX_PER_WINDOW:
                skipped.append(post)
                continue

            selected.append(post)
            if len(selected) >= limit:
                return selected

        seen = {str(post.id) for post in selected}
        for post in skipped:
            if str(post.id) in seen:
                continue
            selected.append(post)
            seen.add(str(post.id))
            if len(selected) >= limit:
                break

        return selected[:limit]

    @staticmethod
    def _ranked_cursor_payload(post, fallback: dict | None = None) -> dict:
        """Build a nested ranked cursor payload, preserving prior state if no post was used."""
        if not post:
            return fallback or {}
        return {
            "id": str(post.id),
            "score": float(getattr(post, "final_score", 0) or 0),
            "ts": post.created_at.isoformat(),
        }

    @staticmethod
    def _chronological_cursor_payload(post, fallback: dict | None = None) -> dict:
        """Build a nested chronological cursor payload, preserving prior state if no post was used."""
        if not post:
            return fallback or {}
        return {
            "id": str(post.id),
            "ts": post.created_at.isoformat(),
        }

    @staticmethod
    def _public_discovery_feed(
        cursor: str | None,
        limit: int,
    ) -> FeedResponseDTO:
        """Feed for unauthenticated users — popular content."""
        qs = FeedService._ranked_queryset()

        if cursor:
            cursor_data = FeedCursor.decode(cursor)
            qs = FeedService._apply_ranked_cursor(qs, cursor_data, cursor)

        candidate_limit = max(limit * 3, limit + 10)
        candidates = list(qs[: candidate_limit + 1])
        posts = FeedService._apply_creator_diversity(candidates, limit)
        has_more = len(candidates) > len(posts)

        post_dtos = FeedService._bulk_build_post_dtos(posts, viewer_id=None)

        next_cursor = None
        if has_more and posts:
            next_cursor = FeedCursor.encode(
                post_id=str(posts[-1].id),
                algo="public",
                created_at=posts[-1].created_at,
                score=getattr(posts[-1], "final_score", 0),
            )

        return FeedResponseDTO(
            posts=post_dtos,
            next_cursor=next_cursor,
            has_more=has_more,
        )

    # =====================================================================
    # Cursor Application
    # =====================================================================

    @staticmethod
    def _apply_cursor(qs, cursor_post_id: str):
        """
        Apply compound (created_at, id) keyset pagination to a queryset.

        Using only ``created_at__lt`` is unsafe when multiple posts share the
        same timestamp (common in tests and rapid mobile submissions): those
        posts get silently skipped on the next page. The compound keyset
        filter handles ties correctly:

            page N+1 = posts where
                (created_at < cursor.created_at)
                OR
                (created_at == cursor.created_at AND id < cursor.id)

        This requires the queryset to be ordered by ``(-created_at, -id)``.
        The cursor value is still just the post UUID (backward-compatible).
        """
        try:
            cursor_post = Post.objects.filter(id=cursor_post_id).values("created_at", "id").first()
            if cursor_post:
                qs = qs.filter(
                    Q(created_at__lt=cursor_post["created_at"])
                    | Q(
                        created_at=cursor_post["created_at"],
                        id__lt=cursor_post["id"],
                    )
                )
        except Exception:  # noqa: BLE001
            # If the cursor ID is invalid/deleted, ignore it and return the
            # unfiltered queryset (effectively a first-page fallback).
            logger.debug("Failed to apply feed cursor: invalid cursor_id")
        return qs

    @staticmethod
    def _apply_ranked_cursor(qs, cursor_data: dict, fallback_cursor: str | None = None):
        """Apply keyset pagination for final_score DESC, created_at DESC, id DESC."""
        cursor_id = cursor_data.get("id") or fallback_cursor
        cursor_score = cursor_data.get("score")
        cursor_ts = parse_datetime(cursor_data.get("ts", "") or "")

        if cursor_id and cursor_score is not None and cursor_ts is not None:
            try:
                cursor_score = float(cursor_score)
                return qs.filter(
                    Q(final_score__lt=cursor_score)
                    | Q(final_score=cursor_score, created_at__lt=cursor_ts)
                    | Q(
                        final_score=cursor_score,
                        created_at=cursor_ts,
                        id__lt=cursor_id,
                    )
                )
            except (TypeError, ValueError):
                logger.debug("Failed to apply ranked feed cursor: invalid score")

        if cursor_id:
            return FeedService._apply_cursor(qs, cursor_id)
        return qs

    @staticmethod
    def _apply_chronological_affinity_cursor(
        qs, cursor_data: dict, fallback_cursor: str | None = None
    ):
        """Apply keyset pagination for created_at DESC, affinity DESC, id DESC."""
        cursor_id = cursor_data.get("id") or fallback_cursor
        cursor_affinity = cursor_data.get("affinity")
        cursor_ts = parse_datetime(cursor_data.get("ts", "") or "")

        if cursor_id and cursor_affinity is not None and cursor_ts is not None:
            try:
                cursor_affinity = int(cursor_affinity)
                return qs.filter(
                    Q(created_at__lt=cursor_ts)
                    | Q(created_at=cursor_ts, creator_affinity__lt=cursor_affinity)
                    | Q(
                        created_at=cursor_ts,
                        creator_affinity=cursor_affinity,
                        id__lt=cursor_id,
                    )
                )
            except (TypeError, ValueError):
                logger.debug("Failed to apply following cursor: invalid affinity")

        if cursor_id:
            return FeedService._apply_cursor(qs, cursor_id)
        return qs

    @staticmethod
    def _apply_following_cursor(
        qs,
        cursor_post_id: str,
        following_ids: list | set,
        *,
        cursor_tier: int | None = None,
    ):
        """
        Compound (is_following, created_at, id) keyset cursor for _returning_user_feed.

        The regular _apply_cursor only handles (created_at, id) which silently drops
        discovery posts that are newer than the cursor's followed-author post.

        **Tier-flip fix (Phase 2):**  If the opaque cursor carries the tier value
        that was computed when the *previous* page was served, we use that frozen
        tier value instead of re-deriving it from the current follow graph.  This
        prevents the "tier flip" bug where a follow/unfollow between pages caused
        massive content jumps.
        """
        try:
            cursor_post = (
                Post.objects.filter(id=cursor_post_id).values("created_at", "id", "user_id").first()
            )
            if cursor_post:
                # Use the tier frozen in the cursor if available (Phase 2 opaque
                # cursor).  Fall back to live derivation for legacy UUID cursors.
                if cursor_tier is not None:
                    frozen_tier = cursor_tier
                else:
                    frozen_tier = (
                        1
                        if str(cursor_post["user_id"]) in {str(fid) for fid in following_ids}
                        else 0
                    )

                qs = qs.filter(
                    Q(is_following__lt=frozen_tier)
                    | Q(
                        is_following=frozen_tier,
                        created_at__lt=cursor_post["created_at"],
                    )
                    | Q(
                        is_following=frozen_tier,
                        created_at=cursor_post["created_at"],
                        id__lt=cursor_post["id"],
                    )
                )
        except Exception:  # noqa: BLE001
            logger.debug("Failed to apply following feed cursor: invalid cursor_id")
        return qs

    # =====================================================================
    # Redis Inbox (Phase 3 — Fan-out-on-Write)
    # =====================================================================

    @staticmethod
    def _get_feed_from_inbox(
        user_id: str,
        raw_cursor: str | None,
        cursor_data: dict,
        limit: int,
    ) -> FeedResponseDTO | None:
        """Try to serve the feed from the user's pre-built Redis inbox.

        Returns ``None`` if the inbox is empty or Redis is unavailable,
        signalling the caller to fall back to the DB algorithm.

        Redis cost: exactly 1 LRANGE + 1 conditional LRANGE per celebrity
        the user follows.  Post hydration is a single batched DB query.
        """
        try:
            from django_redis import get_redis_connection
        except ImportError:
            return None

        try:
            redis_conn = get_redis_connection("default")
        except Exception:
            return None

        # ---- Determine pagination offset within the inbox ----
        # The inbox cursor is a simple integer offset (not a post ID)
        # because the inbox is a stable, pre-sorted Redis list.
        inbox_offset = 0
        if raw_cursor and cursor_data.get("algo") == "inbox":
            inbox_offset = cursor_data.get("offset", 0)

        inbox_key = f"feed:inbox:{user_id}"

        try:
            # Read slightly more than needed so we can set has_more.
            raw_ids = redis_conn.lrange(inbox_key, inbox_offset, inbox_offset + limit)
        except Exception:
            return None

        if not raw_ids:
            return None  # Empty inbox — fall back to DB.

        # Decode bytes → strings.
        post_ids = [pid.decode() if isinstance(pid, bytes) else str(pid) for pid in raw_ids]

        # ---- Celebrity merge ----
        # If the user follows any celebrity accounts, merge their recent
        # posts into the inbox IDs.
        following_ids = FollowSelector.get_following_ids(user_id)
        celebrity_post_ids = FeedService._get_celebrity_posts(redis_conn, following_ids)
        if celebrity_post_ids:
            # Merge and deduplicate, preserving chronological order.
            seen = set(post_ids)
            for cpid in celebrity_post_ids:
                if cpid not in seen:
                    post_ids.append(cpid)
                    seen.add(cpid)

        # ---- Hydrate from DB ----
        # Fetch the actual Post objects, filtering out deleted/hidden posts.
        hydrated_qs = (
            Post.objects.select_related("user")
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

        hydrated_qs = FeedService._exclude_hidden_posts(hydrated_qs, user_id)
        posts_by_id = {str(p.id): p for p in hydrated_qs}

        # Maintain the inbox ordering (newest first).
        posts = [posts_by_id[pid] for pid in post_ids if pid in posts_by_id]

        # Pagination — check if there are more items in the inbox.
        has_more = len(raw_ids) > limit
        posts = posts[:limit]

        if not posts:
            return None  # All inbox posts were deleted/hidden — DB fallback.

        post_dtos = FeedService._bulk_build_post_dtos(posts, user_id)

        next_cursor = None
        if has_more:
            next_cursor = FeedCursor.encode(
                post_id=str(posts[-1].id),
                algo="inbox",
                created_at=posts[-1].created_at,
            )
            # Encode the inbox offset so the next page can pick up where
            # this one left off.
            # Re-encode with the offset baked in.
            payload = FeedCursor.decode(next_cursor)
            payload["offset"] = inbox_offset + limit
            next_cursor = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()

        return FeedResponseDTO(
            posts=post_dtos,
            next_cursor=next_cursor,
            has_more=has_more,
        )

    @staticmethod
    def _get_celebrity_posts(redis_conn, following_ids: list[str]) -> list[str]:
        """Fetch recent post IDs from celebrity authors the user follows.

        Uses a pipeline to batch ZREVRANGE calls — 1 round-trip regardless
        of how many celebrities the user follows.

        Returns:
            List of post ID strings, newest first.
        """
        if not following_ids:
            return []

        # Only check celebrities (those with a feed:celebrity: key).
        # We optimistically pipeline ZREVRANGE for all followed users'
        # celebrity keys — non-existent keys return empty lists (free).
        celeb_keys = [f"feed:celebrity:{fid}" for fid in following_ids]

        try:
            pipe = redis_conn.pipeline(transaction=False)
            for key in celeb_keys:
                pipe.zrevrange(key, 0, 19)  # last 20 posts per celebrity
            results = pipe.execute()

            merged = []
            seen = set()
            for result in results:
                if not result:
                    continue
                for pid in result:
                    decoded = pid.decode() if isinstance(pid, bytes) else str(pid)
                    if decoded not in seen:
                        merged.append(decoded)
                        seen.add(decoded)
            return merged

        except Exception:
            logger.debug("celebrity_post_fetch_failed")
            return []

    # =====================================================================
    # Empty State & Bulk DTO Building
    # =====================================================================

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
        followed_by_user_ids: set = set()

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

            followed_by_user_ids = set(
                Follow.objects.filter(
                    follower_id__in=author_ids, following_id=viewer_id
                ).values_list("follower_id", flat=True)
            )
            followed_by_user_ids = {str(uid) for uid in followed_by_user_ids}

        return [
            PostService._build_post_dto(
                post=p,
                media_items=list(p.media_files.all()),
                viewer_id=viewer_id,
                liked_post_ids=liked_post_ids,
                saved_post_ids=saved_post_ids,
                following_user_ids=following_user_ids,
                followed_by_user_ids=followed_by_user_ids,
            )
            for p in posts
        ]

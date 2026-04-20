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

from django.db.models import Count, Exists, F, OuterRef, Q
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

# Celebrity threshold — must match the value in tasks.py.
CELEBRITY_FOLLOWER_THRESHOLD = 50_000

# Maximum number of IDs to read from a Redis inbox in a single LRANGE.
_INBOX_READ_LIMIT = 60


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

    VERSION = 1

    @staticmethod
    def encode(
        post_id: str,
        algo: str,
        created_at,
        *,
        tier: int | None = None,
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
        return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()

    @staticmethod
    def decode(cursor: str) -> dict:
        """Decode an opaque cursor, falling back to legacy UUID format."""
        if not cursor:
            return {}
        try:
            raw = base64.urlsafe_b64decode(cursor.encode()).decode()
            data = json.loads(raw)
            if data.get("v") == FeedCursor.VERSION:
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

        # ------------------------------------------------------------------
        # Fast path: Redis inbox (Phase 3)
        # ------------------------------------------------------------------
        # Only attempt the inbox for returning users (new users need the
        # engagement-score algorithm for cold-start).  Also skip if the
        # cursor explicitly says it was generated by a DB algorithm — this
        # prevents mixing inbox pages with DB pages mid-scroll.
        cursor_data = FeedCursor.decode(cursor) if cursor else {}
        cursor_algo = cursor_data.get("algo")

        if not is_new_user and cursor_algo not in ("new",):
            inbox_result = FeedService._get_feed_from_inbox(user_id, cursor, cursor_data, limit)
            if inbox_result is not None:
                return inbox_result

        # ------------------------------------------------------------------
        # DB fallback
        # ------------------------------------------------------------------
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
            # Always include -id as a tiebreaker so the compound keyset cursor
            # (_apply_cursor) can page deterministically even if two posts share
            # the exact same created_at timestamp.
            .order_by("-created_at", "-id")
        )

        qs = FeedService._exclude_hidden_posts(qs, user_id)

        if cursor:
            cursor_data = FeedCursor.decode(cursor)
            qs = FeedService._apply_cursor(qs, cursor_data.get("id", cursor))

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

        qs = FeedService._exclude_hidden_posts(qs, user_id)

        # Always include -id as a tiebreaker for deterministic keyset pagination.
        qs = qs.order_by("-created_at", "-id")

        if cursor:
            cursor_data = FeedCursor.decode(cursor)
            qs = FeedService._apply_cursor(qs, cursor_data.get("id", cursor))

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

        next_cursor = None
        if has_more and posts:
            next_cursor = FeedCursor.encode(
                post_id=str(posts[-1].id),
                algo="discover",
                created_at=posts[-1].created_at,
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
            # Filter by category slug, not by the Category FK (UUID).
            # `user_interests` stores slug strings (e.g. "love", "trust"), so
            # category__in would compare UUIDs against strings and never match.
            qs = qs.filter(Q(category__slug__in=user_interests) | Q(category__isnull=True))

        qs = FeedService._exclude_hidden_posts(qs, user_id)

        # Always include -id as a tiebreaker for deterministic keyset pagination.
        qs = qs.order_by("-engagement_score", "-created_at", "-id")

        if cursor:
            cursor_data = FeedCursor.decode(cursor)
            qs = FeedService._apply_cursor(qs, cursor_data.get("id", cursor))

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

        next_cursor = None
        if has_more and posts:
            next_cursor = FeedCursor.encode(
                post_id=str(posts[-1].id),
                algo="new",
                created_at=posts[-1].created_at,
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
                # Sort followed authors' posts first, then by recency.
                # Always include -id as a final tiebreaker so the keyset cursor
                # can page correctly even when two posts share the same timestamp.
            ).order_by("-is_following", "-created_at", "-id")
        else:
            # No follows yet — pure reverse-chronological with tiebreaker.
            qs = qs.order_by("-created_at", "-id")

        qs = FeedService._exclude_hidden_posts(qs, user_id)

        if cursor:
            cursor_data = FeedCursor.decode(cursor)
            cursor_post_id = cursor_data.get("id", cursor)
            cursor_tier = cursor_data.get("tier")

            if following_ids:
                qs = FeedService._apply_following_cursor(
                    qs, cursor_post_id, following_ids, cursor_tier=cursor_tier
                )
            else:
                qs = FeedService._apply_cursor(qs, cursor_post_id)

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

        # Determine the tier of the last post for cursor encoding.
        last_post = posts[-1]
        last_tier = None
        if following_ids:
            last_tier = 1 if str(last_post.user_id) in {str(fid) for fid in following_ids} else 0

        next_cursor = None
        if has_more and posts:
            next_cursor = FeedCursor.encode(
                post_id=str(last_post.id),
                algo="returning",
                created_at=last_post.created_at,
                tier=last_tier,
            )

        return FeedResponseDTO(
            posts=post_dtos,
            next_cursor=next_cursor,
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
            # Always include -id as a tiebreaker for deterministic keyset pagination.
            .order_by("-engagement_score", "-created_at", "-id")
        )

        if cursor:
            cursor_data = FeedCursor.decode(cursor)
            qs = FeedService._apply_cursor(qs, cursor_data.get("id", cursor))

        posts = list(qs[: limit + 1])
        has_more = len(posts) > limit
        posts = posts[:limit]

        post_dtos = FeedService._bulk_build_post_dtos(posts, viewer_id=None)

        next_cursor = None
        if has_more and posts:
            next_cursor = FeedCursor.encode(
                post_id=str(posts[-1].id),
                algo="public",
                created_at=posts[-1].created_at,
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

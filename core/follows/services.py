"""
Follow service — business logic for following and unfollowing users.

Handles self-follow prevention, cache invalidation,
and interest-based creator suggestions.
"""

import logging
from datetime import timedelta

from django.core.cache import cache
from django.db import IntegrityError
from django.db.models import (
    Case,
    Count,
    Exists,
    ExpressionWrapper,
    F,
    IntegerField,
    OuterRef,
    Q,
    Value,
    When,
)
from django.utils import timezone

from core.follows.models import Follow
from core.shared.decorators import rate_limit
from core.shared.dtos import AuthorDTO, FollowResponseDTO
from core.shared.exceptions import ErrorCode, FollowError

logger = logging.getLogger("core.follows")

# Below this length a search would match most of the table, so we return nothing
# instead of scanning it.
MIN_SEARCH_QUERY_LENGTH = 2
MAX_SEARCH_PAGE_SIZE = 50

# Guest suggestions are identical for every visitor, so one cached value serves
# them all — worth doing on a public unauthenticated endpoint (CLAUDE.md §14).
GUEST_SUGGESTIONS_CACHE_KEY = "suggested_creators:guest:v1"
GUEST_SUGGESTIONS_CACHE_TTL = 900

# Engagement is scored across three joined relations, so the pool is narrowed
# first rather than annotating the whole user table.
CANDIDATE_POOL_SIZE = 100

# Share of the guest list ranked purely on engagement. The remainder is reserved
# for creators who post but are not yet established.
GUEST_POPULAR_SLOT_RATIO = 0.6

# A creator above this follower count no longer needs the reserved slots.
FRESH_FOLLOWER_CEILING = 50


class FollowService:
    """Service handling follow/unfollow and social graph operations."""

    @staticmethod
    @rate_limit(max_requests=30, window_seconds=60)
    def follow_user(follower_id: str, following_id: str) -> FollowResponseDTO:
        """Follow a user.

        Args:
            follower_id: UUID of the user who wants to follow.
            following_id: UUID of the user to follow.

        Returns:
            FollowResponseDTO with success status.

        Raises:
            FollowError: If self-follow or already following.
        """
        from core.users.models import User

        if str(follower_id) == str(following_id):
            raise FollowError(
                message="You cannot follow yourself.",
                code=ErrorCode.CANNOT_FOLLOW_SELF,
            )

        target = User.objects.filter(id=following_id, deleted_at__isnull=True).first()
        if not target:
            raise FollowError(
                message="User not found.",
                code=ErrorCode.USER_NOT_FOUND,
            )

        try:
            Follow.objects.create(
                follower_id=follower_id,
                following_id=following_id,
            )
            logger.info(
                "user_followed",
                extra={
                    "follower_id": follower_id,
                    "following_id": following_id,
                },
            )

            FollowService._invalidate_follow_cache(follower_id, following_id)
            FollowService._notify_new_follower(follower_id, following_id)

            return FollowResponseDTO(success=True, following=True)
        except IntegrityError as e:
            raise FollowError(
                message="You are already following this user.",
                code=ErrorCode.ALREADY_FOLLOWING,
            ) from e

    @staticmethod
    def unfollow_user(follower_id: str, following_id: str) -> FollowResponseDTO:
        """Unfollow a user.

        Args:
            follower_id: UUID of the follower.
            following_id: UUID of the user to unfollow.

        Returns:
            FollowResponseDTO with success status.
        """
        deleted_count, _ = Follow.objects.filter(
            follower_id=follower_id,
            following_id=following_id,
        ).delete()

        if deleted_count:
            logger.info(
                "user_unfollowed",
                extra={
                    "follower_id": follower_id,
                    "following_id": following_id,
                },
            )
            FollowService._invalidate_follow_cache(follower_id, following_id)

        return FollowResponseDTO(success=True, following=False)

    @staticmethod
    def _notify_new_follower(follower_id: str, following_id: str) -> None:
        """Send one follower notification per follower/followed pair per day."""
        try:
            from core.notifications.constants import NOTIFICATION_TEMPLATES
            from core.notifications.models import Notification, NotificationType
            from core.notifications.services import create_notification
            from core.users.models import User

            since = timezone.now() - timedelta(hours=24)
            recently_notified = Notification.objects.filter(
                user_id=following_id,
                sender_id=follower_id,
                notification_type=NotificationType.NEW_FOLLOWER,
                reference_type="user",
                reference_id=follower_id,
                created_at__gte=since,
            ).exists()
            if recently_notified:
                return

            follower = User.objects.only("id", "username", "full_name").get(id=follower_id)
            actor_name = follower.username or follower.full_name or "Someone"
            create_notification(
                user_id=following_id,
                type_str=NotificationType.NEW_FOLLOWER,
                reference_id=follower.id,
                reference_type="user",
                title="New Follower",
                message=NOTIFICATION_TEMPLATES[NotificationType.NEW_FOLLOWER].format(
                    username=actor_name
                ),
                sender_id=follower.id,
            )
        except Exception:
            logger.warning(
                "new_follower_notification_failed",
                extra={"follower_id": follower_id, "following_id": following_id},
                exc_info=True,
            )

    @staticmethod
    def get_followers(
        user_id: str,
        viewer_id: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> dict:
        """Get paginated list of followers for a user.

        Args:
            user_id: UUID of the user whose followers to fetch.
            viewer_id: Optional viewer for mutual follow status.
            cursor: Cursor for pagination.
            limit: Page size.

        Returns:
            Dict with users, next_cursor, has_more.
        """
        limit = min(limit, 50)

        qs = (
            Follow.objects.select_related("follower")
            .filter(
                following_id=user_id,
                follower__deleted_at__isnull=True,
                follower__lifecycle_state="active",
            )
            # -id tiebreaker for deterministic compound keyset pagination.
            .order_by("-created_at", "-id")
        )

        if cursor:
            try:
                cursor_follow = Follow.objects.filter(id=cursor).values("created_at", "id").first()
                if cursor_follow:
                    from django.db.models import Q

                    qs = qs.filter(
                        Q(created_at__lt=cursor_follow["created_at"])
                        | Q(
                            created_at=cursor_follow["created_at"],
                            id__lt=cursor_follow["id"],
                        )
                    )
            except Exception:  # noqa: S110
                pass

        follows = list(qs[: limit + 1])
        has_more = len(follows) > limit
        follows = follows[:limit]

        # Normalise to set[str] — UUID objects vs strings compare correctly
        # in Python today, but str() makes this future-proof and explicit.
        mutual_ids: set[str] = set()
        if viewer_id:
            follower_ids = [str(f.follower_id) for f in follows]
            mutual_ids = {
                str(uid)
                for uid in Follow.objects.filter(
                    follower_id=viewer_id,
                    following_id__in=follower_ids,
                ).values_list("following_id", flat=True)
            }

        users = []
        for f in follows:
            user = f.follower
            users.append(
                {
                    "user": AuthorDTO(
                        id=str(user.id),
                        username=user.username or "",
                        avatar_url=user.avatar_url or None,
                    ),
                    # Use str-normalised set for type-safe membership test.
                    "is_following": str(user.id) in mutual_ids,
                }
            )

        return {
            "users": users,
            "next_cursor": str(follows[-1].id) if has_more and follows else None,
            "has_more": has_more,
        }

    @staticmethod
    def get_following(
        user_id: str,
        viewer_id: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> dict:
        """Get paginated list of users that a user follows.

        Args:
            user_id: UUID of the user.
            viewer_id: Optional viewer for mutual follow status.
            cursor: Cursor for pagination.
            limit: Page size.

        Returns:
            Dict with users, next_cursor, has_more.
        """
        limit = min(limit, 50)

        qs = (
            Follow.objects.select_related("following")
            .filter(
                follower_id=user_id,
                following__deleted_at__isnull=True,
                following__lifecycle_state="active",
            )
            # -id tiebreaker for deterministic compound keyset pagination.
            .order_by("-created_at", "-id")
        )

        if cursor:
            try:
                cursor_follow = Follow.objects.filter(id=cursor).values("created_at", "id").first()
                if cursor_follow:
                    from django.db.models import Q

                    qs = qs.filter(
                        Q(created_at__lt=cursor_follow["created_at"])
                        | Q(
                            created_at=cursor_follow["created_at"],
                            id__lt=cursor_follow["id"],
                        )
                    )
            except Exception:  # noqa: S110
                pass

        follows = list(qs[: limit + 1])
        has_more = len(follows) > limit
        follows = follows[:limit]

        # Build the mutual-follow set regardless of whether viewer == user.
        # This gives the true live is_following state for every entry, including
        # the user's own following list (previously hardcoded to True, which was wrong
        # after an unfollow action during the same session).
        mutual_ids: set[str] = set()
        if viewer_id:
            following_ids = [str(f.following_id) for f in follows]
            mutual_ids = {
                str(uid)
                for uid in Follow.objects.filter(
                    follower_id=viewer_id,
                    following_id__in=following_ids,
                ).values_list("following_id", flat=True)
            }

        users = []
        for f in follows:
            user = f.following
            users.append(
                {
                    "user": AuthorDTO(
                        id=str(user.id),
                        username=user.username or "",
                        avatar_url=user.avatar_url or None,
                    ),
                    # Always reflect true live state — never hardcode True.
                    "is_following": str(user.id) in mutual_ids,
                }
            )

        return {
            "users": users,
            "next_cursor": str(follows[-1].id) if has_more and follows else None,
            "has_more": has_more,
        }

    @staticmethod
    def _creator_candidate_queryset(exclude_ids: set, allowed_statuses: list[str]):
        """Followable creators, annotated with follower and post counts.

        Shared by the signed-in and guest suggestion paths so the two cannot
        drift on what counts as a followable account. ``allowed_statuses`` is
        the only difference between them today: guests never see accounts under
        a moderation warning.
        """
        from core.users.models import User, UserRole

        return (
            User.objects.filter(deleted_at__isnull=True)
            .filter(is_active=True, role=UserRole.USER, status__in=allowed_statuses)
            .exclude(username__isnull=True)
            .exclude(username="")
            .exclude(id__in=exclude_ids)
            .annotate(
                followers_count=Count(
                    "follower_set",
                    filter=Q(
                        follower_set__follower__deleted_at__isnull=True,
                        follower_set__follower__lifecycle_state="active",
                    ),
                    distinct=True,
                ),
                posts_count=Count(
                    "posts",
                    filter=Q(posts__deleted_at__isnull=True),
                    distinct=True,
                ),
            )
        )

    @staticmethod
    def _suggestion_dict(user) -> dict:
        return {
            "user": AuthorDTO(
                id=str(user.id),
                username=user.username or "",
                avatar_url=user.avatar_url or None,
            ),
            "bio": user.bio or None,
            "followers_count": user.followers_count,
            # Already annotated by _creator_candidate_queryset; it used to be
            # computed and thrown away, so stats.posts always reported zero.
            "posts_count": user.posts_count,
        }

    @staticmethod
    def get_suggested_creators(
        user_id: str | None,
        limit: int = 10,
    ) -> list[dict]:
        """Suggest creators to follow.

        Signed in: ranked on the viewer's interests and follow graph.
        Guest: one shared list — see _guest_suggested_creators.
        """
        if not user_id:
            return FollowService._guest_suggested_creators(limit)
        return FollowService._personalised_suggested_creators(user_id, limit)

    @staticmethod
    def _personalised_suggested_creators(
        user_id: str,
        limit: int = 10,
    ) -> list[dict]:
        """Get suggested creators based on user interests.

        Orders by follower count and filters out already-followed users.

        Args:
            user_id: UUID of the requesting user.
            limit: Number of suggestions.

        Returns:
            List of dicts with user info and follower_count.
        """
        from core.users.models import UserInterest

        user_interests = list(
            UserInterest.objects.filter(user_id=user_id).values_list("interest", flat=True)
        )

        following_ids = set(
            Follow.objects.filter(
                follower_id=user_id,
                following__deleted_at__isnull=True,
                following__lifecycle_state="active",
            ).values_list("following_id", flat=True)
        )
        following_ids.add(user_id)

        qs = FollowService._creator_candidate_queryset(
            exclude_ids=following_ids,
            allowed_statuses=["active", "warned"],
        ).order_by("-followers_count", "-posts_count", "-created_at")

        if user_interests:
            qs = qs.annotate(
                has_matching_interest=Exists(
                    UserInterest.objects.filter(
                        user_id=OuterRef("id"),
                        interest__in=user_interests,
                    )
                )
            ).order_by(
                "-has_matching_interest",
                "-followers_count",
                "-posts_count",
                "-created_at",
            )

        return [FollowService._suggestion_dict(user) for user in qs[:limit]]

    @staticmethod
    def _guest_suggested_creators(limit: int = 10) -> list[dict]:
        """One shared list for everyone browsing without an account.

        A guest gives us nothing to personalise on, so the result is the same
        for everybody — which makes it cacheable, and makes the ranking matter.
        Ordered purely by follower count, the same few accounts would collect
        every new visitor's attention forever: the rich-get-richer loop that put
        43 posts permanently out of the For You feed. So the list reserves slots
        for creators who are actively posting but not yet established.

        Reserved slots rather than a score bonus: a bonus sits on the same axis
        as the thing it is competing with and is eventually outgrown, while a
        slot cannot be starved no matter how large the corpus gets.
        """
        cache_key = f"{GUEST_SUGGESTIONS_CACHE_KEY}:{limit}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        suggestions = FollowService._build_guest_suggestions(limit)
        cache.set(cache_key, suggestions, timeout=GUEST_SUGGESTIONS_CACHE_TTL)
        return suggestions

    @staticmethod
    def _build_guest_suggestions(limit: int) -> list[dict]:
        from django.db.models import Max

        # Stage 1 — narrow to a bounded pool before touching engagement. The
        # engagement annotations join three multi-valued relations through
        # posts; running them across the whole user table would not scale.
        pool_ids = list(
            FollowService._creator_candidate_queryset(
                exclude_ids=set(),
                allowed_statuses=["active"],
            )
            # An empty profile wastes a slot and makes a poor first impression.
            .filter(posts_count__gt=0)
            .order_by("-followers_count", "-created_at")
            .values_list("id", flat=True)[:CANDIDATE_POOL_SIZE]
        )
        if not pool_ids:
            return []

        # Stage 2 — score only the pool. distinct=True on every Count: three
        # multi-valued joins off `posts` otherwise multiply each other, and a
        # creator with 2 posts and 2 likes would score as though they had 4.
        scored = list(
            FollowService._creator_candidate_queryset(
                exclude_ids=set(),
                allowed_statuses=["active"],
            )
            .filter(id__in=pool_ids)
            .annotate(
                likes_total=Count(
                    "posts__likes",
                    filter=Q(posts__deleted_at__isnull=True),
                    distinct=True,
                ),
                comments_total=Count(
                    "posts__comments",
                    filter=Q(
                        posts__deleted_at__isnull=True,
                        posts__comments__deleted_at__isnull=True,
                    ),
                    distinct=True,
                ),
                shares_total=Count(
                    "posts__shares",
                    filter=Q(posts__deleted_at__isnull=True),
                    distinct=True,
                ),
                last_post_at=Max(
                    "posts__created_at",
                    filter=Q(posts__deleted_at__isnull=True),
                ),
            )
            .annotate(
                # Same weighting as the feed's engagement_score, so "engaging
                # creator" and "engaging post" cannot mean two different things.
                engagement_score=ExpressionWrapper(
                    F("likes_total") + (F("comments_total") * 2) + (F("shares_total") * 3),
                    output_field=IntegerField(),
                )
            )
        )

        popular_slots = max(1, round(limit * GUEST_POPULAR_SLOT_RATIO))
        by_engagement = sorted(
            scored,
            key=lambda u: (-u.engagement_score, -u.followers_count, -u.created_at.timestamp()),
        )
        newcomers = sorted(
            (u for u in scored if u.followers_count < FRESH_FOLLOWER_CEILING),
            key=lambda u: -(u.last_post_at.timestamp() if u.last_post_at else 0),
        )

        selected: list = []
        seen: set = set()
        for pool, take in ((by_engagement, popular_slots), (newcomers, limit - popular_slots)):
            for user in pool:
                if len(selected) >= limit or take <= 0:
                    break
                if user.id in seen:
                    continue
                selected.append(user)
                seen.add(user.id)
                take -= 1

        # Backfill so a thin newcomer pool never shortens the list.
        for user in by_engagement:
            if len(selected) >= limit:
                break
            if user.id not in seen:
                selected.append(user)
                seen.add(user.id)

        return [FollowService._suggestion_dict(user) for user in selected]

    @staticmethod
    def search_creators(
        query: str,
        viewer_id: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """Search creators by username or full name for the Discover screen.

        Ranked so the most likely match surfaces first: exact username, then
        prefix matches, then anywhere-in-the-string, breaking ties by follower
        count. Returns the same fields as get_suggested_creators() so the client
        can reuse its creator card.

        Args:
            query: Search text. Queries shorter than MIN_SEARCH_QUERY_LENGTH
                return no results rather than scanning the whole user table.
            viewer_id: Optional viewer, used to exclude self and flag isFollowing.
            page: 1-based page number.
            page_size: Results per page (capped at MAX_SEARCH_PAGE_SIZE).

        Returns:
            Dict with creators, total_count, page, page_size and has_more.
        """
        from core.users.models import User, UserRole

        page = max(1, page)
        page_size = max(1, min(page_size, MAX_SEARCH_PAGE_SIZE))
        term = (query or "").strip()

        empty = {
            "creators": [],
            "total_count": 0,
            "page": page,
            "page_size": page_size,
            "has_more": False,
        }
        if len(term) < MIN_SEARCH_QUERY_LENGTH:
            return empty

        qs = (
            User.objects.filter(deleted_at__isnull=True)
            .filter(is_active=True, role=UserRole.USER, status__in=["active", "warned"])
            .filter(lifecycle_state="active")
            .exclude(username__isnull=True)
            .exclude(username="")
            .filter(Q(username__icontains=term) | Q(full_name__icontains=term))
        )
        if viewer_id:
            qs = qs.exclude(id=viewer_id)

        following_ids: set = set()
        if viewer_id:
            following_ids = set(
                Follow.objects.filter(follower_id=viewer_id).values_list("following_id", flat=True)
            )

        qs = qs.annotate(
            followers_count=Count(
                "follower_set",
                filter=Q(
                    follower_set__follower__deleted_at__isnull=True,
                    follower_set__follower__lifecycle_state="active",
                ),
                distinct=True,
            ),
            posts_count=Count("posts", filter=Q(posts__deleted_at__isnull=True), distinct=True),
            # Relevance buckets (0 sorts first): exact handle, then prefix, then
            # anywhere. Keeps "john" ahead of "notjohnny" for a "john" search.
            match_rank=Case(
                When(username__iexact=term, then=Value(0)),
                When(username__istartswith=term, then=Value(1)),
                When(full_name__istartswith=term, then=Value(2)),
                default=Value(3),
                output_field=IntegerField(),
            ),
        ).order_by("match_rank", "-followers_count", "-posts_count", "username")

        total_count = qs.count()
        offset = (page - 1) * page_size
        results = list(qs[offset : offset + page_size])

        creators = [
            {
                "user": AuthorDTO(
                    id=str(user.id),
                    username=user.username or "",
                    avatar_url=user.avatar_url or None,
                ),
                "bio": user.bio or None,
                "followers_count": user.followers_count,
                "posts_count": user.posts_count,
                "is_following": user.id in following_ids,
            }
            for user in results
        ]

        return {
            "creators": creators,
            "total_count": total_count,
            "page": page,
            "page_size": page_size,
            "has_more": offset + len(results) < total_count,
        }

    @staticmethod
    def _invalidate_follow_cache(follower_id: str, following_id: str) -> None:
        """Invalidate cached follow data.

        Clears both the social-graph caches (follower/following ID lists, is_following
        flag) AND the `me` profile cache for both parties so that `followersCount` and
        `followingCount` reflect the new state immediately instead of being stale for
        up to 5 minutes.
        """
        try:
            from django.core.cache import cache

            cache.delete_many(
                [
                    f"followers:{following_id}",
                    f"following:{follower_id}",
                    f"is_following:{follower_id}:{following_id}",
                    # Invalidate both users' `me` responses so follower/following
                    # counts are fresh on the next authenticated query.
                    f"user_me_data_{follower_id}",
                    f"user_me_data_{following_id}",
                ]
            )
        except Exception:
            logger.warning("Follow cache invalidation failed")

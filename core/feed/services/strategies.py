"""Feed composition strategies — new/returning user, blending, diversity.

Split from the former core/feed/services.py (no behavior change).
"""

import logging

from django.db.models import (
    Q,
)

from core.follows.selectors import FollowSelector
from core.shared.dtos import (
    EmptyStateDTO,
    FeedResponseDTO,
)

logger = logging.getLogger("core.feed")

from core.feed.services.builders import (  # noqa: E402
    _bulk_build_post_dtos,
    _get_empty_state_suggestions,
)
from core.feed.services.cursors import (  # noqa: E402
    FeedCursor,
    _apply_cursor,
    _apply_ranked_cursor,
    _chronological_cursor_payload,
    _ranked_cursor_payload,
)
from core.feed.services.ranking import (  # noqa: E402
    CREATOR_DIVERSITY_MAX_PER_WINDOW,
    CREATOR_DIVERSITY_WINDOW,
    DISCOVERY_BLEND_SIZE,
    FOLLOWED_BLEND_SIZE,
    REPORT_SUPPRESSION_THRESHOLD,
    _base_post_queryset,
    _exclude_hidden_posts,
    _ranked_queryset,
    _with_engagement_counts,
)


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

    qs = _ranked_queryset()

    if user_interests:
        # Filter by category slug, not by the Category FK (UUID).
        # `user_interests` stores slug strings (e.g. "love", "trust"), so
        # category__in would compare UUIDs against strings and never match.
        qs = qs.filter(Q(category__slug__in=user_interests) | Q(category__isnull=True))

    qs = _exclude_hidden_posts(qs, user_id)

    if cursor:
        cursor_data = FeedCursor.decode(cursor)
        qs = _apply_ranked_cursor(qs, cursor_data, cursor)

    candidate_limit = max(limit * 3, limit + 10)
    candidates = list(qs[: candidate_limit + 1])
    posts = _apply_creator_diversity(candidates, limit)
    has_more = len(candidates) > len(posts)

    if not posts:
        suggestions = _get_empty_state_suggestions(user_id)
        return FeedResponseDTO(
            posts=[],
            has_more=False,
            empty_state=EmptyStateDTO(
                message="Welcome to Ziona! Explore and follow creators.",
                suggestions=suggestions,
            ),
        )

    post_dtos = _bulk_build_post_dtos(posts, user_id)

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

    discovery_qs = _ranked_queryset()
    if following_ids_set:
        discovery_qs = discovery_qs.exclude(user_id__in=following_ids_set)
    discovery_qs = _exclude_hidden_posts(discovery_qs, user_id)

    legacy_cursor = (
        cursor_data if cursor_data.get("id") and not cursor_data.get("discovery") else {}
    )
    discovery_cursor = cursor_data.get("discovery") or legacy_cursor
    if discovery_cursor:
        discovery_qs = _apply_ranked_cursor(discovery_qs, discovery_cursor)

    followed_qs = _with_engagement_counts(
        _base_post_queryset().filter(user_id__in=following_ids)
    ).filter(unique_reports_count__lt=REPORT_SUPPRESSION_THRESHOLD)
    followed_qs = _exclude_hidden_posts(followed_qs, user_id).order_by("-created_at", "-id")

    followed_cursor = cursor_data.get("followed") or legacy_cursor
    if followed_cursor:
        followed_qs = _apply_cursor(followed_qs, followed_cursor.get("id"))

    discovery_candidates = list(discovery_qs[: candidate_limit + 1])
    followed_candidates = list(followed_qs[: candidate_limit + 1]) if following_ids else []

    blended = _blend_discovery_and_followed(
        discovery_candidates,
        followed_candidates,
        target_count=max(limit * 3, limit + 10),
        start_slot=start_slot,
    )
    posts = _apply_creator_diversity(blended, limit)

    has_more = (
        len(discovery_candidates) > candidate_limit
        or len(followed_candidates) > candidate_limit
        or len(blended) > len(posts)
    )

    if not posts:
        suggestions = _get_empty_state_suggestions(user_id)
        return FeedResponseDTO(
            posts=[],
            has_more=False,
            empty_state=EmptyStateDTO(
                message="No posts yet. Follow creators to fill your feed!",
                suggestions=suggestions,
            ),
        )

    post_dtos = _bulk_build_post_dtos(posts, user_id)

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
            discovery=_ranked_cursor_payload(last_discovery, discovery_cursor),
            followed=_chronological_cursor_payload(last_followed, followed_cursor),
        )

    return FeedResponseDTO(
        posts=post_dtos,
        next_cursor=next_cursor,
        has_more=has_more,
    )


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
        wants_followed = slot % (DISCOVERY_BLEND_SIZE + FOLLOWED_BLEND_SIZE) >= DISCOVERY_BLEND_SIZE
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


def _public_discovery_feed(
    cursor: str | None,
    limit: int,
) -> FeedResponseDTO:
    """Feed for unauthenticated users — popular content."""
    qs = _ranked_queryset()

    if cursor:
        cursor_data = FeedCursor.decode(cursor)
        qs = _apply_ranked_cursor(qs, cursor_data, cursor)

    candidate_limit = max(limit * 3, limit + 10)
    candidates = list(qs[: candidate_limit + 1])
    posts = _apply_creator_diversity(candidates, limit)
    has_more = len(candidates) > len(posts)

    post_dtos = _bulk_build_post_dtos(posts, viewer_id=None)

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

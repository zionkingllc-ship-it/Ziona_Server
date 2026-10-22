"""Feed composition strategies — new/returning user, blending, diversity.

The For You strategies (new user, returning user, guest) run per session: see
core.feed.services.session for what a session fixes, and
core.feed.services.assembly for how pages are filled without repeats or loss.
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
from core.shared.utils import parse_uuid

logger = logging.getLogger("core.feed")

from core.feed.services.assembly import MAX_CARRY, assemble_page  # noqa: E402
from core.feed.services.builders import (  # noqa: E402
    _bulk_build_post_dtos,
    _get_empty_state_suggestions,
)
from core.feed.services.cursors import (  # noqa: E402
    FeedCursor,
    _apply_ranked_cursor,
    _apply_session_followed_cursor,
    _apply_session_ranked_cursor,
    _session_followed_position,
    _session_ranked_position,
)
from core.feed.services.ranking import (  # noqa: E402
    DISCOVERY_BLEND_SIZE,
    FOLLOWED_BLEND_SIZE,
    REPORT_SUPPRESSION_THRESHOLD,
    _base_post_queryset,
    _exclude_hidden_posts,
    _session_ranked_queryset,
    _with_engagement_counts,
    _with_seen_rank,
)
from core.feed.services.session import FeedSession, ServedHistory  # noqa: E402

BLEND_PERIOD = DISCOVERY_BLEND_SIZE + FOLLOWED_BLEND_SIZE


def _resume_session(cursor: str | None) -> tuple[FeedSession, dict]:
    """The session a request belongs to, and its saved state.

    No cursor means the app is opening the feed: a new session, so a new order.
    A cursor from before sessions existed, or one that fails validation, also
    starts afresh rather than continuing a position it cannot interpret.
    """
    state = FeedCursor.decode(cursor) if cursor else {}
    session = FeedSession.from_payload(state.get("session"))
    if session is None:
        return FeedSession.start(), {}
    return session, state


def _carry_ids(state: dict) -> list[str]:
    raw = state.get("carry")
    if not isinstance(raw, list):
        return []
    ids = (parse_uuid(value) for value in raw[:MAX_CARRY])
    return [str(post_id) for post_id in ids if post_id is not None]


def _fetch_carried(querysets, ids: list[str]) -> list:
    """Re-load carried posts in their saved order, still subject to every filter.

    A carried post deleted, hidden or reported since the last page drops out
    here — the carry list can never surface something the feed would not.
    """
    if not ids:
        return []
    found = {}
    for qs in querysets:
        for post in qs.filter(id__in=ids):
            found.setdefault(str(post.id), post)
    return [found[post_id] for post_id in ids if post_id in found]


def _encode_session_cursor(algo: str, session: FeedSession, last_post, **state) -> str:
    return FeedCursor.encode(
        post_id=str(last_post.id),
        algo=algo,
        created_at=last_post.created_at,
        session=session.to_payload(),
        **state,
    )


def _ranked_session_page(qs, state: dict, limit: int):
    """One page of a single ranked stream (new-user and guest feeds).

    Returns (assembled page, has_more, next ranked position).
    """
    carried = _fetch_carried([qs], _carry_ids(state))
    stream = _apply_session_ranked_cursor(qs, state.get("ranked")).exclude(
        id__in=[post.id for post in carried]
    )
    window = max(limit * 3, limit + 10)
    fetched = list(stream[: window + 1])

    assembled = assemble_page(carried, fetched[:window], limit)
    consumed = fetched[: assembled.consumed]
    position = _session_ranked_position(consumed[-1]) if consumed else state.get("ranked")
    has_more = bool(assembled.carry) or assembled.consumed < len(fetched)
    return assembled, has_more, position


def _ranked_page(qs, cursor: str | None, limit: int, algo: str, *, vary_kinds: bool = True):
    """One page of a plain ranked listing (Discover, search) — no session, no shuffle.

    Same assembly as For You, so held-back posts are carried instead of lost and
    the cursor marks the last post *consumed* (not merely the last one shown).
    Returns (posts, has_more, next_cursor).
    """
    state = FeedCursor.decode(cursor) if cursor else {}
    carried = _fetch_carried([qs], _carry_ids(state))
    stream = _apply_ranked_cursor(qs, state, cursor) if cursor else qs
    stream = stream.exclude(id__in=[post.id for post in carried])
    window = max(limit * 3, limit + 10)
    fetched = list(stream[: window + 1])

    assembled = assemble_page(carried, fetched[:window], limit, vary_kinds=vary_kinds)
    posts = assembled.posts
    consumed = fetched[: assembled.consumed]
    has_more = bool(assembled.carry) or assembled.consumed < len(fetched)

    next_cursor = None
    if has_more and posts:
        carry = [str(post.id) for post in assembled.carry]
        if consumed:
            last = consumed[-1]
            next_cursor = FeedCursor.encode(
                post_id=str(last.id),
                algo=algo,
                created_at=last.created_at,
                score=getattr(last, "final_score", 0),
                carry=carry,
            )
        else:
            # The page came wholly from the carry: the stream has not moved.
            next_cursor = FeedCursor.encode(
                post_id=state.get("id"),
                algo=algo,
                created_at=state.get("ts"),
                score=state.get("score"),
                carry=carry,
            )
    return posts, has_more, next_cursor


def _new_user_feed(
    user_id: str,
    cursor: str | None,
    limit: int,
) -> FeedResponseDTO:
    """Feed for new users (<7 days) — popular content, matched to their interests."""
    from core.users.models import UserInterest

    session, state = _resume_session(cursor)
    history = ServedHistory.load(user_id, session)

    user_interests = list(
        UserInterest.objects.filter(user_id=user_id).values_list("interest", flat=True)
    )

    qs = _session_ranked_queryset(session, history.served_before(session))

    if user_interests:
        # Filter by category slug, not by the Category FK (UUID).
        # `user_interests` stores slug strings (e.g. "love", "trust"), so
        # category__in would compare UUIDs against strings and never match.
        qs = qs.filter(Q(category__slug__in=user_interests) | Q(category__isnull=True))

    qs = _exclude_hidden_posts(qs, user_id)

    assembled, has_more, position = _ranked_session_page(qs, state, limit)
    posts = assembled.posts

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

    history.record((post.id for post in posts), session)
    post_dtos = _bulk_build_post_dtos(posts, user_id)

    next_cursor = None
    if has_more:
        next_cursor = _encode_session_cursor(
            "new",
            session,
            posts[-1],
            ranked=position,
            carry=[str(post.id) for post in assembled.carry],
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
    """Feed for returning users — discovery-first 3:1 blend.

    Discovery is the session-ranked catalogue minus followed creators. Followed
    posts stay chronological, except that any served before the session move
    behind the unseen ones — so a reopen does not lead with the same followed
    posts either.
    """
    session, state = _resume_session(cursor)
    history = ServedHistory.load(user_id, session)
    seen_ids = history.served_before(session)

    following_ids = FollowSelector.get_following_ids(user_id)
    following_ids_set = {str(fid) for fid in following_ids}
    start_slot = state.get("slot", 0)
    if not isinstance(start_slot, int) or not 0 <= start_slot < BLEND_PERIOD:
        start_slot = 0

    discovery_qs = _session_ranked_queryset(session, seen_ids)
    if following_ids_set:
        discovery_qs = discovery_qs.exclude(user_id__in=following_ids_set)
    discovery_qs = _exclude_hidden_posts(discovery_qs, user_id)

    followed_qs = _with_engagement_counts(
        _base_post_queryset().filter(user_id__in=following_ids)
    ).filter(unique_reports_count__lt=REPORT_SUPPRESSION_THRESHOLD)
    followed_qs = _with_seen_rank(_exclude_hidden_posts(followed_qs, user_id), seen_ids)
    followed_qs = followed_qs.order_by("seen_rank", "-created_at", "-id")

    carried = _fetch_carried([discovery_qs, followed_qs], _carry_ids(state))
    carried_ids = [post.id for post in carried]

    window = max(limit * 5, 40)
    discovery_fetched = list(
        _apply_session_ranked_cursor(discovery_qs, state.get("ranked")).exclude(id__in=carried_ids)[
            : window + 1
        ]
    )
    followed_fetched = (
        list(
            _apply_session_followed_cursor(followed_qs, state.get("followed")).exclude(
                id__in=carried_ids
            )[: window + 1]
        )
        if following_ids
        else []
    )

    blended = _blend_discovery_and_followed(
        discovery_fetched[:window],
        followed_fetched[:window],
        target_count=max(limit * 3, limit + 10),
        start_slot=start_slot,
    )
    assembled = assemble_page(carried, blended, limit)
    posts = assembled.posts

    has_more = bool(assembled.carry) or assembled.consumed < (
        len(discovery_fetched) + len(followed_fetched)
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

    history.record((post.id for post in posts), session)
    post_dtos = _bulk_build_post_dtos(posts, user_id)

    # Each stream continues after the last post this page consumed from it.
    consumed = blended[: assembled.consumed]
    consumed_discovery = [p for p in consumed if str(p.user_id) not in following_ids_set]
    consumed_followed = [p for p in consumed if str(p.user_id) in following_ids_set]

    next_cursor = None
    if has_more:
        next_cursor = _encode_session_cursor(
            "returning",
            session,
            posts[-1],
            ranked=(
                _session_ranked_position(consumed_discovery[-1])
                if consumed_discovery
                else state.get("ranked")
            ),
            followed=(
                _session_followed_position(consumed_followed[-1])
                if consumed_followed
                else state.get("followed")
            ),
            slot=(start_slot + assembled.consumed) % BLEND_PERIOD,
            carry=[str(post.id) for post in assembled.carry],
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


def _public_discovery_feed(
    cursor: str | None,
    limit: int,
) -> FeedResponseDTO:
    """Feed for unauthenticated users — popular content, shuffled per session.

    A guest has no identity to remember, so there is no "already shown" demotion;
    each open still gets its own order.
    """
    session, state = _resume_session(cursor)
    qs = _session_ranked_queryset(session, seen_ids=())

    assembled, has_more, position = _ranked_session_page(qs, state, limit)
    posts = assembled.posts

    post_dtos = _bulk_build_post_dtos(posts, viewer_id=None)

    next_cursor = None
    if has_more and posts:
        next_cursor = _encode_session_cursor(
            "public",
            session,
            posts[-1],
            ranked=position,
            carry=[str(post.id) for post in assembled.carry],
        )

    return FeedResponseDTO(
        posts=post_dtos,
        next_cursor=next_cursor,
        has_more=has_more,
    )

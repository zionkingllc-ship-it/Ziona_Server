"""Whole-session invariants for the For You feed.

A "session" is one scroll: the first page (no cursor) and every page reached by
following ``next_cursor`` until ``has_more`` is False. Whatever ranking,
shuffling or diversity rules run, a session must show every eligible post
exactly once — no repeats while scrolling, and nothing silently lost.
"""

import base64
import json
from datetime import timedelta

import pytest
from django.utils import timezone

from core.feed.services import FeedService
from core.feed.services.session import FeedSession

PAGE_SIZE = 5


def _make_user(create_user, name: str, *, returning: bool = True):
    from core.users.models import User

    user = create_user(email=f"{name}@feed.test", username=name)
    if returning:
        User.objects.filter(id=user.id).update(created_at=timezone.now() - timedelta(days=30))
        user.refresh_from_db()
    return user


@pytest.fixture
def catalogue(create_user):
    """A catalogue the diversity rules actually bite on.

    One prolific creator owns a third of the posts, and video dominates the mix —
    the shape of the live catalogue — so pages cannot simply be filled in rank
    order without breaking the creator and media-kind rules.
    """
    from core.engagement.models import Like
    from core.posts.models import Post

    voters = [_make_user(create_user, f"voter{i}") for i in range(8)]
    creators = [_make_user(create_user, f"creator{i}") for i in range(6)]
    kinds = ["video", "video", "image", "video", "text", "video", "image"]
    posts = []
    for index in range(30):
        author = creators[0] if index % 3 == 0 else creators[1 + index % 5]
        post = Post.objects.create(
            user=author, post_type=kinds[index % len(kinds)], caption=f"post {index}"
        )
        Post.objects.filter(id=post.id).update(
            created_at=timezone.now() - timedelta(hours=1 + index * 7)
        )
        for voter in voters[: index % 6]:
            Like.objects.create(user=voter, post=post)
        posts.append(post)
    return {"posts": posts, "creators": creators}


def _scroll(fetch_page) -> list[str]:
    """Follow next_cursor from the first page to the end; return post ids in order."""
    shown: list[str] = []
    cursor = None
    for _ in range(100):
        page = fetch_page(cursor)
        shown.extend(str(post.id) for post in page.posts)
        if not page.has_more:
            return shown
        assert page.next_cursor, "has_more without a cursor"
        cursor = page.next_cursor
    pytest.fail("feed never ended")


def _assert_exactly_once(shown: list[str], expected: set[str]):
    repeats = len(shown) - len(set(shown))
    assert repeats == 0, f"{repeats} post(s) shown twice in one session"
    missing = expected - set(shown)
    assert not missing, f"{len(missing)} eligible post(s) never shown"


def test_guest_session_shows_every_post_exactly_once(catalogue):
    shown = _scroll(lambda cursor: FeedService.get_feed(None, cursor, PAGE_SIZE))

    _assert_exactly_once(shown, {str(post.id) for post in catalogue["posts"]})


def test_new_user_session_shows_every_post_exactly_once(catalogue, create_user):
    viewer = _make_user(create_user, "newcomer", returning=False)

    shown = _scroll(lambda cursor: FeedService.get_for_you_feed(str(viewer.id), cursor, PAGE_SIZE))

    _assert_exactly_once(shown, {str(post.id) for post in catalogue["posts"]})


def test_returning_user_session_shows_every_post_exactly_once(catalogue, create_user):
    from core.follows.models import Follow

    viewer = _make_user(create_user, "regular")
    for creator in catalogue["creators"][:2]:
        Follow.objects.create(follower=viewer, following=creator)

    shown = _scroll(lambda cursor: FeedService.get_for_you_feed(str(viewer.id), cursor, PAGE_SIZE))

    _assert_exactly_once(shown, {str(post.id) for post in catalogue["posts"]})


# ---------------------------------------------------------------------------
# The invariant holds under every setting, and with history from earlier opens
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("strength", "penalty", "kind_run"),
    [(0.0, 1.0, 0), (0.5, 0.25, 2), (0.9, 0.01, 1)],
    ids=["all-off", "defaults", "extreme"],
)
def test_every_setting_keeps_sessions_exact(
    catalogue, create_user, settings, strength, penalty, kind_run
):
    settings.FEED_SHUFFLE_STRENGTH = strength
    settings.FEED_SEEN_PENALTY = penalty
    settings.FEED_MAX_SAME_KIND_RUN = kind_run
    viewer = _make_user(create_user, "tuned")
    expected = {str(post.id) for post in catalogue["posts"]}

    # A first, partial open seeds the "already shown" history...
    FeedService.get_for_you_feed(str(viewer.id), None, PAGE_SIZE)
    # ...so the session under test runs with a non-empty demoted set.
    shown = _scroll(lambda cursor: FeedService.get_for_you_feed(str(viewer.id), cursor, PAGE_SIZE))

    _assert_exactly_once(shown, expected)


# ---------------------------------------------------------------------------
# What the PM asked for
# ---------------------------------------------------------------------------


def _first_page(user_id, session: FeedSession, monkeypatch) -> list[str]:
    monkeypatch.setattr(FeedSession, "start", classmethod(lambda cls: session))
    page = FeedService.get_for_you_feed(str(user_id), None, PAGE_SIZE * 2)
    return [str(post.id) for post in page.posts]


def test_guest_session_parameters_determine_the_order(catalogue, settings, monkeypatch):
    settings.FEED_SEEN_PENALTY = 1.0
    now = timezone.now().timestamp()

    def guest_page(session):
        monkeypatch.setattr(FeedSession, "start", classmethod(lambda cls: session))
        return [str(post.id) for post in FeedService.get_feed(None, None, PAGE_SIZE * 2).posts]

    first = guest_page(FeedSession(31_337, 0.25, now))
    again = guest_page(FeedSession(31_337, 0.25, now))
    other = guest_page(FeedSession(77_001, 0.71, now))

    assert first == again, "guest order with identical session parameters must be deterministic"
    assert first != other, "different session parameters should vary the guest line-up"


def test_reopening_leads_with_posts_not_already_shown(catalogue, create_user, settings):
    settings.FEED_SHUFFLE_STRENGTH = 0.0  # isolate the seen penalty from the shuffle
    settings.FEED_SEEN_PENALTY = 0.01
    viewer = _make_user(create_user, "reopener")

    before = {
        str(p.id) for p in FeedService.get_for_you_feed(str(viewer.id), None, PAGE_SIZE).posts
    }
    after = {str(p.id) for p in FeedService.get_for_you_feed(str(viewer.id), None, PAGE_SIZE).posts}

    assert before and after
    assert before.isdisjoint(after), "a reopen showed the posts it had just shown"


def test_shown_posts_recover_after_the_seen_window(catalogue, create_user, settings, monkeypatch):
    import time as real_time

    from core.feed.services import session as session_module

    settings.FEED_SHUFFLE_STRENGTH = 0.0
    settings.FEED_SEEN_PENALTY = 0.01
    settings.FEED_SEEN_WINDOW_HOURS = 24
    viewer = _make_user(create_user, "returner")
    # Hold the shuffle/lead choice fixed so this checks history expiry alone.
    monkeypatch.setattr(
        FeedSession,
        "start",
        classmethod(lambda cls: FeedSession(31_337, 0.0, session_module._now())),
    )

    first = [str(p.id) for p in FeedService.get_for_you_feed(str(viewer.id), None, PAGE_SIZE).posts]
    later = real_time.time() + 25 * 60 * 60
    monkeypatch.setattr(session_module.time, "time", lambda: later)
    next_day = [
        str(p.id) for p in FeedService.get_for_you_feed(str(viewer.id), None, PAGE_SIZE).posts
    ]

    assert next_day == first, "a post shown yesterday should rank normally again"


def test_media_kinds_are_mixed_where_the_catalogue_allows(catalogue, create_user, settings):
    settings.FEED_MAX_SAME_KIND_RUN = 2
    viewer = _make_user(create_user, "mixer")

    page = FeedService.get_for_you_feed(str(viewer.id), None, 10).posts
    kinds = [post.type for post in page]
    longest = max(_runs(kinds))

    assert longest <= 2, f"same kind {longest} times in a row: {kinds}"


def _runs(items: list) -> list[int]:
    runs, length = [], 0
    for index, item in enumerate(items):
        length = length + 1 if index and item == items[index - 1] else 1
        runs.append(length)
    return runs or [0]


# ---------------------------------------------------------------------------
# Compatibility and robustness
# ---------------------------------------------------------------------------


def test_all_settings_off_restores_the_plain_ranked_order(catalogue, create_user, settings):
    from core.feed.services.ranking import _ranked_queryset

    settings.FEED_SHUFFLE_STRENGTH = 0.0
    settings.FEED_SEEN_PENALTY = 1.0
    settings.FEED_MAX_SAME_KIND_RUN = 0
    plain = [str(post.id) for post in _ranked_queryset()]

    shown = _scroll(lambda cursor: FeedService.get_feed(None, cursor, PAGE_SIZE))

    # Creator spacing still reorders within a page, so the session order itself is
    # what must equal the ranked one; the scroll must still cover all of it.
    from core.feed.services.ranking import _session_ranked_queryset

    session_order = [
        str(post.id) for post in _session_ranked_queryset(FeedSession.start(), seen_ids=())
    ]
    assert session_order == plain
    assert sorted(shown) == sorted(plain)


def _cursor(payload: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


@pytest.mark.parametrize(
    "payload",
    [
        {"v": 3, "id": "x", "session": "not-a-dict"},
        {"v": 3, "session": {"m": 5, "o": 0.5, "t": 1}},  # out of range, ancient
        {"v": 3, "session": {"m": 50_000, "o": 0.5, "t": 4_102_444_800}},  # far future
        {"v": 3, "session": {"m": "x", "o": None, "t": []}},
    ],
    ids=["session-not-object", "stale-and-out-of-range", "future", "wrong-types"],
)
def test_malformed_session_starts_a_fresh_one(catalogue, payload):
    page = FeedService.get_feed(None, _cursor(payload), PAGE_SIZE)

    assert len(page.posts) == PAGE_SIZE


def test_a_cursor_from_before_sessions_starts_a_fresh_session(catalogue):
    legacy = _cursor({"v": 3, "id": str(catalogue["posts"][3].id), "algo": "public", "ts": "x"})

    page = FeedService.get_feed(None, legacy, PAGE_SIZE)

    assert len(page.posts) == PAGE_SIZE


def test_carry_cannot_surface_a_post_the_viewer_hid(catalogue, create_user):
    from core.engagement.models import HiddenPost

    viewer = _make_user(create_user, "hider")
    hidden = catalogue["posts"][0]
    HiddenPost.objects.create(user=viewer, post=hidden)
    first = FeedService.get_for_you_feed(str(viewer.id), None, PAGE_SIZE)
    state = json.loads(base64.urlsafe_b64decode(first.next_cursor.encode()))
    state["carry"] = [str(hidden.id)] + state.get("carry", [])

    second = FeedService.get_for_you_feed(str(viewer.id), _cursor(state), PAGE_SIZE)

    assert str(hidden.id) not in {str(post.id) for post in second.posts}


def test_feed_survives_a_cache_outage(catalogue, create_user, monkeypatch):
    from django.core.cache import cache

    def down(*args, **kwargs):
        raise ConnectionError("redis unavailable")

    monkeypatch.setattr(cache, "get", down)
    monkeypatch.setattr(cache, "set", down)
    viewer = _make_user(create_user, "offline")

    page = FeedService.get_for_you_feed(str(viewer.id), None, PAGE_SIZE)

    assert len(page.posts) == PAGE_SIZE


def test_each_page_costs_one_cache_read_and_one_write(catalogue, create_user, monkeypatch):
    """The Upstash budget: served history adds exactly two commands per page."""
    from django.core.cache import cache

    from core.feed.services import session as session_module

    calls = []
    real_get, real_set = cache.get, cache.set

    def counting(name, real):
        def wrapper(key, *args, **kwargs):
            if str(key).startswith("feed:served:"):
                calls.append(name)
            return real(key, *args, **kwargs)

        return wrapper

    # `cache` is a shared proxy, so count only the served-history key.
    monkeypatch.setattr(session_module.cache, "get", counting("get", real_get))
    monkeypatch.setattr(session_module.cache, "set", counting("set", real_set))
    viewer = _make_user(create_user, "budget")

    FeedService.get_for_you_feed(str(viewer.id), None, PAGE_SIZE)

    assert calls == ["get", "set"]


def test_migration_gives_existing_posts_independent_shuffle_keys(catalogue):
    from importlib import import_module

    from django.apps import apps

    from core.posts.models import Post

    Post.all_objects.update(shuffle_key=0.5)  # what AddField alone leaves behind
    migration = import_module("core.posts.migrations.0006_post_shuffle_key")

    migration.backfill_shuffle_keys(apps, None)

    keys = list(Post.all_objects.values_list("shuffle_key", flat=True))
    assert len(set(keys)) == len(keys)
    assert all(0.0 <= key < 1.0 for key in keys)


def test_a_session_never_demotes_its_own_pages_despite_clock_skew(monkeypatch):
    """Page 2 may be served by a server whose clock is behind the one that served page 1.

    The session's own pages must never count as "already shown" for it — but they
    must for the next session.
    """
    import uuid

    from core.feed.services import session as session_module
    from core.feed.services.session import ServedHistory

    user_id = str(uuid.uuid4())
    post_id = str(uuid.uuid4())
    session = FeedSession(31_337, 0.25, started_at=1_000_000.0)
    monkeypatch.setattr(session_module.time, "time", lambda: 999_999.5)  # clock behind start

    ServedHistory.load(user_id, session).record([post_id], session)

    assert ServedHistory.load(user_id, session).served_before(session) == []
    next_session = FeedSession(40_000, 0.5, started_at=1_000_100.0)
    assert ServedHistory.load(user_id, next_session).served_before(next_session) == [post_id]


def test_a_session_longer_than_the_history_cap_stays_exact(catalogue, create_user, monkeypatch):
    """Regression: the history cap must never shrink a session's frozen "already shown" set.

    Recording a long session used to evict the oldest entries — exactly the
    pre-session ones the session had frozen as already shown. Those posts then
    lost their demotion mid-scroll, jumped above the cursor and were never shown.
    """
    from core.feed.services import session as session_module

    monkeypatch.setattr(session_module, "SERVED_HISTORY_MAX", 8)
    viewer = _make_user(create_user, "longscroller")
    expected = {str(post.id) for post in catalogue["posts"]}

    for _ in range(3):  # earlier opens fill the history past its cap
        FeedService.get_for_you_feed(str(viewer.id), None, PAGE_SIZE)
    shown = _scroll(lambda cursor: FeedService.get_for_you_feed(str(viewer.id), cursor, PAGE_SIZE))

    _assert_exactly_once(shown, expected)


# ---------------------------------------------------------------------------
# Discover and search share the page assembly (but not the shuffle)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("media_type", [None, "video"], ids=["all-kinds", "videos-only"])
def test_discover_scroll_shows_every_post_exactly_once(catalogue, media_type):
    shown = _scroll(
        lambda cursor: FeedService.get_discover_feed(
            None, media_type=media_type, cursor=cursor, limit=PAGE_SIZE
        )
    )

    expected = {
        str(post.id)
        for post in catalogue["posts"]
        if media_type is None or post.post_type == media_type
    }
    _assert_exactly_once(shown, expected)


def test_search_scroll_shows_every_match_exactly_once(catalogue):
    shown = _scroll(
        lambda cursor: FeedService.search_discover_content(
            "post", None, cursor=cursor, limit=PAGE_SIZE
        )
    )

    _assert_exactly_once(shown, {str(post.id) for post in catalogue["posts"]})


def test_discover_and_search_keep_their_ranked_order(catalogue):
    """No shuffle outside For You: browsing and search stay stable between opens."""
    first = [str(p.id) for p in FeedService.get_discover_feed(None, limit=10).posts]
    again = [str(p.id) for p in FeedService.get_discover_feed(None, limit=10).posts]

    assert first == again


# ---------------------------------------------------------------------------
# Page assembly in isolation
# ---------------------------------------------------------------------------


def _fake_posts(spec: str):
    """'a:video b:video ...' -> stand-ins with an author and a kind."""
    from types import SimpleNamespace

    return [
        SimpleNamespace(id=index, user_id=author, post_type=kind)
        for index, (author, kind) in enumerate(item.split(":") for item in spec.split())
    ]


def test_assembly_relaxes_the_kind_rule_before_creator_spacing():
    """Only one kind available: kinds must repeat, but creators must still alternate."""
    from core.feed.services.assembly import assemble_page

    candidates = _fake_posts("a:text a:text a:text b:text b:text b:text c:text c:text")

    page = assemble_page([], candidates, limit=6).posts

    authors = [post.user_id for post in page]
    assert len(page) == 6
    assert all(x != y for x, y in zip(authors, authors[1:], strict=False)), authors


def test_assembly_still_fills_a_page_from_a_single_creator():
    from core.feed.services.assembly import assemble_page

    candidates = _fake_posts("a:video a:video a:video a:video")

    assembled = assemble_page([], candidates, limit=4)

    assert [post.id for post in assembled.posts] == [0, 1, 2, 3]
    assert assembled.carry == []


# ---------------------------------------------------------------------------
# Quality-gated first slot and strict unseen ordering
# ---------------------------------------------------------------------------


def test_seen_post_cannot_lead_when_unseen_posts_exist(catalogue, create_user, settings):
    settings.FEED_SHUFFLE_STRENGTH = 0
    settings.FEED_SEEN_PENALTY = 1  # hard unseen ordering must work without a score penalty
    viewer = _make_user(create_user, "unseenfirst")

    first = FeedService.get_for_you_feed(str(viewer.id), None, 5)
    second = FeedService.get_for_you_feed(str(viewer.id), None, 5)
    first_ids = {str(post.id) for post in first.posts}
    second_ids = {str(post.id) for post in second.posts}

    assert len(first_ids) == len(second_ids) == 5
    assert first_ids.isdisjoint(second_ids)


def test_two_qualified_posts_rotate_even_without_seen_history(create_user, settings, monkeypatch):
    from core.posts.models import Post

    settings.FEED_SHUFFLE_STRENGTH = 0
    settings.FEED_SEEN_WINDOW_HOURS = 0
    viewer = _make_user(create_user, "topviewer")
    for i in range(2):
        author = _make_user(create_user, f"topauthor{i}")
        Post.objects.create(user=author, post_type="text", caption=f"post {i}")
    monkeypatch.setattr(
        FeedSession,
        "start",
        classmethod(lambda cls: FeedSession(31_337, 0.0, timezone.now().timestamp())),
    )

    first = FeedService.get_for_you_feed(str(viewer.id), None, 1)
    second = FeedService.get_for_you_feed(str(viewer.id), None, 1)

    assert first.posts[0].id != second.posts[0].id


def test_guest_lead_varies_when_two_posts_share_quality_band(create_user, settings, monkeypatch):
    from core.posts.models import Post

    settings.FEED_SHUFFLE_STRENGTH = 0
    for i in range(2):
        author = _make_user(create_user, f"guestauthor{i}")
        Post.objects.create(user=author, post_type="text", caption=f"post {i}")

    def first_id(offset):
        monkeypatch.setattr(
            FeedSession,
            "start",
            classmethod(lambda cls: FeedSession(31_337, offset, timezone.now().timestamp())),
        )
        return FeedService.get_feed(None, None, 1).posts[0].id

    assert first_id(0.0) != first_id(0.99)


def test_quality_gate_keeps_dominant_post_above_low_quality_alternative(
    create_user, settings, monkeypatch
):
    from core.engagement.models import Like
    from core.posts.models import Post

    settings.FEED_SHUFFLE_STRENGTH = 0
    strong_author = _make_user(create_user, "strongauthor")
    weak_author = _make_user(create_user, "weakauthor")
    strong = Post.objects.create(user=strong_author, post_type="text", caption="strong")
    Post.objects.create(user=weak_author, post_type="text", caption="weak")
    for i in range(8):
        voter = _make_user(create_user, f"qualityvoter{i}")
        Like.objects.create(user=voter, post=strong)
    monkeypatch.setattr(
        FeedSession,
        "start",
        classmethod(lambda cls: FeedSession(31_337, 0.99, timezone.now().timestamp())),
    )

    assert FeedService.get_feed(None, None, 1).posts[0].id == str(strong.id)


def test_unseen_followed_post_precedes_seen_discovery_post(create_user, settings, monkeypatch):
    from core.follows.models import Follow
    from core.posts.models import Post

    settings.FEED_SHUFFLE_STRENGTH = 0
    settings.FEED_SEEN_PENALTY = 1
    viewer = _make_user(create_user, "blendviewer")
    discovery_author = _make_user(create_user, "discoveryauthor")
    followed_author = _make_user(create_user, "followedauthor")
    discovery = Post.objects.create(user=discovery_author, post_type="text", caption="discovery")
    followed = Post.objects.create(user=followed_author, post_type="text", caption="followed")
    Follow.objects.create(follower=viewer, following=followed_author)
    monkeypatch.setattr(
        FeedSession,
        "start",
        classmethod(lambda cls: FeedSession(31_337, 0.0, timezone.now().timestamp())),
    )

    first = FeedService.get_for_you_feed(str(viewer.id), None, 1)
    second = FeedService.get_for_you_feed(str(viewer.id), None, 1)

    assert first.posts[0].id == str(discovery.id)
    assert second.posts[0].id == str(followed.id)


def test_rotated_lead_preserves_cursor_replay_and_complete_scroll(
    catalogue, create_user, settings, monkeypatch
):
    settings.FEED_SHUFFLE_STRENGTH = 0
    settings.FEED_SEEN_WINDOW_HOURS = 0
    viewer = _make_user(create_user, "longscroll")
    monkeypatch.setattr(
        FeedSession,
        "start",
        classmethod(lambda cls: FeedSession(31_337, 0.99, timezone.now().timestamp())),
    )
    first = FeedService.get_for_you_feed(str(viewer.id), None, 1)
    assert first.next_cursor
    assert len(first.next_cursor) < 2048
    replay_a = FeedService.get_for_you_feed(str(viewer.id), first.next_cursor, 1)
    replay_b = FeedService.get_for_you_feed(str(viewer.id), first.next_cursor, 1)
    assert [p.id for p in replay_a.posts] == [p.id for p in replay_b.posts]
    assert replay_a.next_cursor == replay_b.next_cursor

    shown = [str(first.posts[0].id)]
    cursor = first.next_cursor
    for _ in range(40):
        page = FeedService.get_for_you_feed(str(viewer.id), cursor, 1)
        shown.extend(str(post.id) for post in page.posts)
        if not page.has_more:
            assert page.next_cursor is None
            break
        cursor = page.next_cursor
        assert len(cursor) < 2048
    else:
        pytest.fail("feed never ended")
    _assert_exactly_once(shown, {str(post.id) for post in catalogue["posts"]})


@pytest.mark.parametrize("count", [1, 2])
def test_sparse_catalogue_rotates_without_duplicates(create_user, count):
    from core.posts.models import Post

    viewer = _make_user(create_user, f"sparseviewer{count}")
    expected = set()
    for i in range(count):
        author = _make_user(create_user, f"sparseauthor{count}{i}")
        post = Post.objects.create(user=author, post_type="text", caption=f"post {i}")
        expected.add(str(post.id))

    first = FeedService.get_for_you_feed(str(viewer.id), None, 1)
    second = FeedService.get_for_you_feed(str(viewer.id), None, 1)
    assert first.posts and second.posts
    if count == 2:
        assert first.posts[0].id != second.posts[0].id
    shown = _scroll(lambda cursor: FeedService.get_for_you_feed(str(viewer.id), cursor, 1))
    _assert_exactly_once(shown, expected)

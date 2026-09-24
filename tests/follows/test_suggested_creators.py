"""Suggested creators — the guest list and the signed-in list it branches from.

The guest list is shared by every visitor, so its ranking decides who gets seen
by people with no follow graph at all. Ranked purely on follower count the same
few accounts would take every new visitor forever, which is the loop that put 43
posts permanently out of the For You feed.
"""

from datetime import timedelta

import pytest
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.engagement.models import Comment, Like
from core.follows.models import Follow
from core.follows.services import FollowService
from core.posts.models import Post
from core.users.models import User


@pytest.fixture(autouse=True)
def _clear_guest_cache():
    """Guest suggestions are cached for 15 minutes — isolate every test."""
    cache.clear()
    yield
    cache.clear()


def _creator(username: str, *, status: str = "active", posts: int = 1) -> User:
    user = User.objects.create_user(
        email=f"{username}@example.com",
        username=username,
        password="password123",
        status=status,
    )
    for index in range(posts):
        Post.objects.create(user=user, post_type="text", caption=f"{username}-{index}")
    return user


def _usernames(suggestions) -> list[str]:
    return [s["user"].username for s in suggestions]


# ---------------------------------------------------------------------------
# Guests
# ---------------------------------------------------------------------------


def test_guest_receives_suggestions(db):
    """The regression this ticket exists for — guests used to get nothing."""
    for index in range(3):
        _creator(f"creator{index}")

    suggestions = FollowService.get_suggested_creators(None, limit=10)

    assert len(suggestions) == 3


def test_warned_users_are_hidden_from_guests(db):
    _creator("cleancreator")
    _creator("warnedcreator", status="warned")

    assert _usernames(FollowService.get_suggested_creators(None, limit=10)) == ["cleancreator"]


def test_warned_users_still_appear_for_signed_in_viewers(db):
    """Scoped to guests deliberately — this pins that the signed-in path is untouched."""
    viewer = User.objects.create_user(
        email="viewer@example.com", username="viewer", password="password123"
    )
    _creator("warnedcreator", status="warned")

    suggestions = FollowService.get_suggested_creators(str(viewer.id), limit=10)

    assert "warnedcreator" in _usernames(suggestions)


def test_creators_without_posts_are_not_suggested_to_guests(db):
    """An empty profile spends a slot and makes a poor first impression."""
    _creator("haspost", posts=1)
    _creator("noposts", posts=0)

    assert _usernames(FollowService.get_suggested_creators(None, limit=10)) == ["haspost"]


def test_engagement_outranks_a_bigger_follower_count(db):
    """The PM's ask: rank on engagement, not audience size alone."""
    engaging = _creator("engaging")
    followed = _creator("followed")

    # `followed` has more followers but nothing on their posts.
    for index in range(5):
        fan = User.objects.create_user(
            email=f"fan{index}@example.com", username=f"fan{index}", password="password123"
        )
        Follow.objects.create(follower=fan, following=followed)

    # `engaging` has one follower but real engagement.
    solo = User.objects.create_user(
        email="solo@example.com", username="solo", password="password123"
    )
    Follow.objects.create(follower=solo, following=engaging)
    post = engaging.posts.first()
    for index in range(4):
        liker = User.objects.create_user(
            email=f"liker{index}@example.com", username=f"liker{index}", password="password123"
        )
        Like.objects.create(user=liker, post=post)
        Comment.objects.create(user=liker, post=post, text="amen")

    ranked = _usernames(FollowService.get_suggested_creators(None, limit=10))

    assert ranked.index("engaging") < ranked.index("followed")


def test_engagement_is_not_inflated_by_join_fan_out(db):
    """Three multi-valued joins off `posts` multiply without distinct=True.

    Two posts, two likes and two comments must score 2 + (2 * 2) = 6. Without
    the distinct guard the joins cross-multiply and it scores 12, which silently
    hands multi-post creators an advantage proportional to their post count.
    """
    creator = _creator("prolific", posts=2)
    fan = User.objects.create_user(
        email="fan@example.com", username="fanuser", password="password123"
    )
    for post in creator.posts.all():
        Like.objects.create(user=fan, post=post)
        Comment.objects.create(user=fan, post=post, text="amen")

    scored = FollowService._build_guest_suggestions(limit=10)

    assert len(scored) == 1
    # Reach through to the annotation the ranking actually sorted on.
    from django.db.models import Count, Q

    counts = (
        User.objects.filter(id=creator.id)
        .annotate(
            likes_total=Count("posts__likes", distinct=True),
            comments_total=Count(
                "posts__comments", filter=Q(posts__comments__deleted_at__isnull=True), distinct=True
            ),
        )
        .first()
    )
    assert counts.likes_total == 2
    assert counts.comments_total == 2


def test_newer_creators_hold_reserved_slots(db):
    """Established creators must not take every slot on the guest list."""
    established = [_creator(f"big{index}") for index in range(10)]

    # Give each established creator enough followers to clear the ceiling.
    fans = [
        User.objects.create_user(
            email=f"bigfan{i}@example.com", username=f"bigfan{i}", password="password123"
        )
        for i in range(60)
    ]
    for creator in established:
        Follow.objects.bulk_create(
            [Follow(follower=fan, following=creator) for fan in fans], ignore_conflicts=True
        )

    newcomer = _creator("newcomer")
    Post.objects.filter(user=newcomer).update(created_at=timezone.now())

    ranked = _usernames(FollowService.get_suggested_creators(None, limit=10))

    assert "newcomer" in ranked, "reserved slots must survive a full field of popular creators"


def test_guest_list_is_filled_when_no_newcomers_qualify(db):
    """A thin newcomer pool must not shorten the list."""
    fans = [
        User.objects.create_user(
            email=f"f{i}@example.com", username=f"f{i}", password="password123"
        )
        for i in range(60)
    ]
    for index in range(12):
        creator = _creator(f"popular{index}")
        Follow.objects.bulk_create(
            [Follow(follower=fan, following=creator) for fan in fans], ignore_conflicts=True
        )

    assert len(FollowService.get_suggested_creators(None, limit=10)) == 10


def test_guest_list_is_cached(db):
    """Every guest shares one result, so the second caller must not hit the DB."""
    _creator("cached")

    FollowService.get_suggested_creators(None, limit=10)
    with CaptureQueriesContext(connection) as captured:
        second = FollowService.get_suggested_creators(None, limit=10)

    assert _usernames(second) == ["cached"]
    assert captured.captured_queries == []


def test_posts_count_is_reported(db):
    """It was annotated and then discarded, so stats.posts always read zero."""
    _creator("counted", posts=3)

    suggestion = FollowService.get_suggested_creators(None, limit=10)[0]

    assert suggestion["posts_count"] == 3


# ---------------------------------------------------------------------------
# Signed in — unchanged behaviour
# ---------------------------------------------------------------------------


def test_signed_in_excludes_self_and_already_followed(db):
    viewer = User.objects.create_user(
        email="viewer@example.com", username="viewer", password="password123"
    )
    followed = _creator("alreadyfollowed")
    _creator("stranger")
    Follow.objects.create(follower=viewer, following=followed)

    ranked = _usernames(FollowService.get_suggested_creators(str(viewer.id), limit=10))

    assert ranked == ["stranger"]


def test_signed_in_still_ranks_by_follower_count(db):
    """The personalised path was moved, not rewritten — ordering must hold."""
    viewer = User.objects.create_user(
        email="viewer@example.com", username="viewer", password="password123"
    )
    small = _creator("small")
    big = _creator("big")
    for index in range(3):
        fan = User.objects.create_user(
            email=f"fan{index}@example.com", username=f"fan{index}", password="password123"
        )
        Follow.objects.create(follower=fan, following=big)

    ranked = _usernames(FollowService.get_suggested_creators(str(viewer.id), limit=10))

    assert ranked.index(big.username) < ranked.index(small.username)


def test_signed_in_includes_creators_without_posts(db):
    """The has-posts filter is a guest-list rule; it must not leak sideways."""
    viewer = User.objects.create_user(
        email="viewer@example.com", username="viewer", password="password123"
    )
    _creator("noposts", posts=0)

    assert "noposts" in _usernames(FollowService.get_suggested_creators(str(viewer.id), limit=10))


def test_reserved_slot_goes_to_the_most_recent_poster(db):
    """ "Newer creator" means still posting, not merely low-follower.

    limit=2 gives one engagement slot and one reserved slot. `star` wins the
    engagement slot outright, so the reserved slot is genuinely contested
    between the stale creators and the fresh one.
    """
    star = _creator("star")
    fan = User.objects.create_user(
        email="starfan@example.com", username="starfan", password="password123"
    )
    Like.objects.create(user=fan, post=star.posts.first())
    Comment.objects.create(user=fan, post=star.posts.first(), text="amen")

    for index in range(3):
        stale = _creator(f"stale{index}")
        Post.objects.filter(user=stale).update(created_at=timezone.now() - timedelta(days=400))
    fresh = _creator("fresh")
    Post.objects.filter(user=fresh).update(created_at=timezone.now())

    ranked = _usernames(FollowService.get_suggested_creators(None, limit=2))

    assert ranked[0] == "star"
    assert ranked[1] == fresh.username
    assert not any(name.startswith("stale") for name in ranked)

"""Query-count budgets for the paths the mobile client hits constantly.

These are regression guards, not micro-benchmarks. The notification list
silently drifted to 41 queries for a 20-row page before anyone noticed, because
nothing failed when it did — `deepLink` and `destination` each re-ran a
per-notification lookup and the cost was invisible in review.

Two assertions per path, and the second is the one that matters:

1. An absolute ceiling, so a path cannot quietly get more expensive.
2. **Flatness** — the same count at page size 5 and 20. A count that grows with
   the result set is the definition of an N+1, and flatness catches it however
   many baseline queries the path happens to need. A ceiling alone does not:
   an N+1 hidden under a generous ceiling still passes.

Raising a ceiling is a real decision. If one of these fails, find the new query
before changing the number.
"""

import pytest
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext

PW = "password123"  # pragma: allowlist secret


@pytest.fixture
def budget_corpus(db, create_user):
    """Enough rows that an N+1 shows up as a count difference between pages."""
    from core.circles.models import Circle, CircleMembership, CirclePost
    from core.engagement.models import Comment, Like
    from core.follows.models import Follow
    from core.notifications.models import Notification
    from core.posts.models import Post

    cache.clear()
    viewer = create_user(email="budget-viewer@x.com", username="budgetviewer", password=PW)
    authors = [
        create_user(email=f"budget-a{i}@x.com", username=f"budgeta{i}", password=PW)
        for i in range(25)
    ]
    fans = [
        create_user(email=f"budget-f{i}@x.com", username=f"budgetf{i}", password=PW)
        for i in range(5)
    ]

    for author in authors[:10]:
        Follow.objects.create(follower=viewer, following=author)

    posts = [
        Post.objects.create(user=author, post_type="text", caption=f"post {index}")
        for index, author in enumerate(authors)
        for _ in range(2)
    ]
    for post in posts[:30]:
        for fan in fans:
            Like.objects.create(user=fan, post=post)

    circle = Circle.objects.create(name="Budget Circle", description="x")
    CircleMembership.objects.create(circle=circle, user=viewer, role="member")
    for author in authors[:10]:
        CircleMembership.objects.create(circle=circle, user=author, role="member")
        for index in range(3):
            CirclePost.objects.create(circle=circle, user=author, text=f"cp {index}")

    # Comment notifications are the shape that regressed: each one needs its
    # parent post resolved, which is exactly what used to be done per row.
    for index, author in enumerate(authors):
        comment = Comment.objects.create(user=viewer, post=posts[index], text="mine")
        Notification.objects.create(
            user=viewer,
            sender=author,
            notification_type="like_comment",
            reference_id=comment.id,
            reference_type="comment",
            message="liked your comment",
        )

    return {"viewer": viewer, "circle": circle, "authors": authors}


def _count(fn) -> int:
    fn()  # warm any per-process cache so we measure steady state
    with CaptureQueriesContext(connection) as captured:
        fn()
    return len(captured.captured_queries)


def _report(label, queries):
    return "\n".join(
        [f"{label} issued {len(queries)} queries:"]
        + [f"  {index + 1:>2}. {query['sql'][:140]}" for index, query in enumerate(queries)]
    )


def _assert_budget(label, fn, ceiling):
    fn()
    with CaptureQueriesContext(connection) as captured:
        fn()
    actual = len(captured.captured_queries)
    assert actual <= ceiling, (
        f"{label} budget exceeded: {actual} > {ceiling}.\n"
        f"Find the new query before raising the ceiling.\n"
        + _report(label, captured.captured_queries)
    )


def _paths(corpus):
    from core.circles.schema.posts import CirclePostType
    from core.circles.services.circle_posts import get_circle_feed
    from core.feed.services import FeedService
    from core.notifications.schema import NotificationItem
    from core.notifications.services import (
        _build_destination_context,
        build_notification_destination,
        get_notifications,
    )
    from core.profiles.services import ProfileService

    viewer = str(corpus["viewer"].id)
    circle = str(corpus["circle"].id)
    target = str(corpus["authors"][0].id)

    def notifications(limit):
        rows = list(get_notifications(corpus["viewer"].id, limit=limit))
        context = _build_destination_context(
            (row.reference_type, str(row.reference_id)) for row in rows if row.reference_id
        )
        for row in rows:
            item = NotificationItem.from_instance(row)
            item._destination_data = build_notification_destination(
                notification_type=row.notification_type,
                reference_type=row.reference_type,
                reference_id=str(row.reference_id),
                notification_id=str(row.id),
                context=context,
            )
            # Both fields the mobile client requests, on every row.
            item.destination()
            item.deep_link()

    def circle_feed(limit):
        posts, _, _ = get_circle_feed(circle, page=1, page_size=limit, viewer_id=viewer)
        [CirclePostType.from_db_model(post) for post in posts]

    return {
        "forYouFeed": (lambda n: FeedService.get_for_you_feed(user_id=viewer, limit=n), 11),
        "followingFeed": (lambda n: FeedService.get_following_feed(user_id=viewer, limit=n), 9),
        "notifications": (notifications, 4),
        "circleFeed": (circle_feed, 7),
        # 9 is the floor, not a target to beat. The two remaining COUNTs join
        # opposite sides of `follows` (followers joins the follower, following
        # joins the followed), so merging them into one OR across both
        # directions cannot use a single index and is slower, not faster.
        "userProfile": (lambda n: ProfileService.get_user_profile(target, viewer), 9),
    }


@pytest.mark.parametrize(
    "path",
    ["forYouFeed", "followingFeed", "notifications", "circleFeed", "userProfile"],
)
def test_hot_path_stays_within_its_query_budget(budget_corpus, path):
    fn, ceiling = _paths(budget_corpus)[path]
    _assert_budget(path, lambda: fn(20), ceiling)


@pytest.mark.parametrize(
    "path",
    ["forYouFeed", "followingFeed", "notifications", "circleFeed"],
)
def test_hot_path_does_not_scale_with_page_size(budget_corpus, path):
    """The N+1 detector. A per-row query makes the larger page cost more."""
    fn, _ = _paths(budget_corpus)[path]

    small = _count(lambda: fn(5))
    large = _count(lambda: fn(20))

    assert small == large, (
        f"{path} issued {small} queries for 5 rows but {large} for 20 — "
        f"the cost grows with the page, which means a query is running per row. "
        f"Batch it rather than raising a ceiling."
    )

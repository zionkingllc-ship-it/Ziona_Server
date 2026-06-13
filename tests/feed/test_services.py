"""Tests for FeedService — For You, Following, and Discover feeds."""

from datetime import timedelta

import pytest
from django.utils import timezone

from core.feed.services import FeedCursor, FeedService


def make_returning_user(user):
    from core.users.models import User

    User.objects.filter(id=user.id).update(created_at=timezone.now() - timedelta(days=8))
    user.refresh_from_db()
    return user


def make_post(user, caption, *, age_hours=1):
    from core.posts.models import Post

    post = Post.objects.create(user=user, post_type="text", caption=caption)
    Post.objects.filter(id=post.id).update(created_at=timezone.now() - timedelta(hours=age_hours))
    post.refresh_from_db()
    return post


def make_users(create_user, prefix, count):
    return [
        create_user(email=f"{prefix}{i}@test.com", username=f"{prefix}{i}") for i in range(count)
    ]


def add_likes(post, users):
    from core.engagement.models import Like

    Like.objects.bulk_create([Like(post=post, user=user) for user in users])


def add_reports(post, users):
    from core.moderation.models import Report, ReportReason, ReportStatus

    Report.objects.bulk_create(
        [
            Report(
                reporter=user,
                post=post,
                target_type="post",
                target_id=post.id,
                reason=ReportReason.OTHER,
                description="Test report",
                status=ReportStatus.PENDING,
            )
            for user in users
        ]
    )


@pytest.fixture
def user_a(create_user):
    return create_user(email="a@test.com", username="user_a")


@pytest.fixture
def user_b(create_user):
    return create_user(email="b@test.com", username="user_b")


@pytest.fixture
def posts(user_a, user_b):
    from core.posts.models import Post

    posts = []
    for i in range(5):
        posts.append(
            Post.objects.create(
                user=user_a,
                post_type="text",
                caption=f"Post {i} by A",
            )
        )
    for i in range(3):
        posts.append(
            Post.objects.create(
                user=user_b,
                post_type="text",
                caption=f"Post {i} by B",
            )
        )
    return posts


class TestForYouFeed:
    """Tests for the For You feed algorithm."""

    def test_returns_posts(self, user_a, user_b, posts):
        result = FeedService.get_for_you_feed(str(user_b.id))
        assert len(result.posts) > 0

    def test_includes_own_posts(self, user_a, posts):
        result = FeedService.get_for_you_feed(str(user_a.id))
        own_posts_present = any(p.author.id == str(user_a.id) for p in result.posts)
        assert own_posts_present, "Expected own posts to be included per user request"

    def test_pagination(self, user_b, posts):
        result = FeedService.get_for_you_feed(str(user_b.id), limit=2)
        assert len(result.posts) <= 2

    def test_returning_user_feed_is_discovery_first_blend(self, create_user):
        from core.follows.services import FollowService

        viewer = make_returning_user(create_user(email="viewer@test.com", username="viewer"))
        followed = create_user(email="followed@test.com", username="followed")
        discovery_authors = make_users(create_user, "disc", 4)

        FollowService.follow_user(str(viewer.id), str(followed.id))

        for index, author in enumerate(discovery_authors):
            make_post(author, f"Discovery {index}", age_hours=index + 1)
        make_post(followed, "Followed 1", age_hours=1)
        make_post(followed, "Followed 2", age_hours=2)

        result = FeedService.get_for_you_feed(str(viewer.id), limit=4)
        authors = [post.author.id for post in result.posts]

        assert len(authors) == 4
        assert all(author_id != str(followed.id) for author_id in authors[:3])
        assert authors[3] == str(followed.id)


class TestFollowingFeed:
    """Tests for the Following feed."""

    def test_empty_following_feed(self, user_a):
        result = FeedService.get_following_feed(str(user_a.id))
        assert len(result.posts) == 0
        assert result.empty_state is not None
        assert result.empty_state.message != ""

    def test_following_feed_with_follows(self, user_a, user_b, posts):
        from core.follows.services import FollowService

        FollowService.follow_user(str(user_b.id), str(user_a.id))
        result = FeedService.get_following_feed(str(user_b.id))
        assert len(result.posts) >= 1

    def test_following_feed_chronological(self, user_a, user_b, posts):
        from core.follows.services import FollowService

        FollowService.follow_user(str(user_b.id), str(user_a.id))
        result = FeedService.get_following_feed(str(user_b.id))

        if len(result.posts) >= 2:
            for i in range(len(result.posts) - 1):
                assert result.posts[i].created_at >= result.posts[i + 1].created_at

    def test_following_feed_excludes_unfollowed_authors(self, create_user):
        from core.follows.services import FollowService

        viewer = create_user(email="viewer-following@test.com", username="viewer_following")
        followed_author = create_user(email="followed-feed@test.com", username="followed_feed")
        unfollowed_author = create_user(
            email="unfollowed-feed@test.com",
            username="unfollowed_feed",
        )

        FollowService.follow_user(str(viewer.id), str(followed_author.id))

        followed_post = make_post(followed_author, "Followed post", age_hours=1)
        unfollowed_post = make_post(unfollowed_author, "Unfollowed post", age_hours=1)

        result = FeedService.get_following_feed(str(viewer.id), limit=10)
        returned_post_ids = {post.id for post in result.posts}
        returned_author_ids = {post.author.id for post in result.posts}

        assert str(followed_post.id) in returned_post_ids
        assert str(unfollowed_post.id) not in returned_post_ids
        assert returned_author_ids == {str(followed_author.id)}


class TestDiscoverFeed:
    """Tests for the Discover feed."""

    def test_discover_returns_posts(self, user_a, user_b, posts):
        result = FeedService.get_discover_feed(str(user_b.id))
        assert len(result.posts) > 0

    def test_discover_includes_own(self, user_a, posts):
        result = FeedService.get_discover_feed(str(user_a.id))
        own_posts_present = any(p.author.id == str(user_a.id) for p in result.posts)
        assert own_posts_present, "Expected own posts to be included per user request"

    def test_freshness_decay_can_outrank_old_engagement(self, create_user):
        old_author = create_user(email="old@test.com", username="old_author")
        fresh_author = create_user(email="fresh@test.com", username="fresh_author")
        voters = make_users(create_user, "freshness_voter", 24)

        old_post = make_post(old_author, "Old viral", age_hours=24 * 8)
        fresh_post = make_post(fresh_author, "Fresh useful", age_hours=1)
        add_likes(old_post, voters[:20])
        add_likes(fresh_post, voters[20:23])

        result = FeedService.get_discover_feed(limit=2)

        assert result.posts[0].id == str(fresh_post.id)
        assert result.posts[1].id == str(old_post.id)

    def test_report_penalty_reduces_distribution_score(self, create_user):
        clean_author = create_user(email="clean@test.com", username="clean_author")
        reported_author = create_user(email="reported@test.com", username="reported_author")
        users = make_users(create_user, "penalty_user", 22)

        clean_post = make_post(clean_author, "Clean post")
        reported_post = make_post(reported_author, "Reported post")
        add_likes(clean_post, users[:10])
        add_likes(reported_post, users[:16])
        add_reports(reported_post, users[16:21])

        result = FeedService.get_discover_feed(limit=2)

        assert result.posts[0].id == str(clean_post.id)
        assert result.posts[1].id == str(reported_post.id)

    def test_report_suppression_excludes_algorithmic_posts(self, create_user):
        visible_author = create_user(email="visible@test.com", username="visible_author")
        suppressed_author = create_user(email="suppressed@test.com", username="suppressed_author")
        users = make_users(create_user, "suppress_user", 12)

        visible_post = make_post(visible_author, "Visible post")
        suppressed_post = make_post(suppressed_author, "Suppressed post")
        add_likes(suppressed_post, users[:11])
        add_reports(suppressed_post, users[:10])

        discover = FeedService.get_discover_feed(limit=10)
        public = FeedService._public_discovery_feed(cursor=None, limit=10)

        assert str(visible_post.id) in [post.id for post in discover.posts]
        assert str(suppressed_post.id) not in [post.id for post in discover.posts]
        assert str(suppressed_post.id) not in [post.id for post in public.posts]

    def test_creator_diversity_avoids_consecutive_posts_when_possible(self, create_user):
        dominant = create_user(email="dominant@test.com", username="dominant")
        alternates = make_users(create_user, "alternate", 4)
        voters = make_users(create_user, "diversity_voter", 20)

        for index in range(5):
            post = make_post(dominant, f"Dominant {index}", age_hours=index + 1)
            add_likes(post, voters[:10])
        for index, author in enumerate(alternates):
            post = make_post(author, f"Alternate {index}", age_hours=index + 1)
            add_likes(post, voters[10:12])

        result = FeedService.get_discover_feed(limit=6)
        author_ids = [post.author.id for post in result.posts]

        assert all(
            author_ids[index] != author_ids[index + 1] for index in range(len(author_ids) - 1)
        )
        assert author_ids[:6].count(str(dominant.id)) <= 2

    def test_ranked_cursor_paginates_without_duplicates(self, create_user):
        authors = make_users(create_user, "cursor_author", 6)
        voters = make_users(create_user, "cursor_voter", 6)

        for index, author in enumerate(authors):
            post = make_post(author, f"Cursor {index}", age_hours=index + 1)
            add_likes(post, voters[: index + 1])

        first_page = FeedService.get_discover_feed(limit=2)
        second_page = FeedService.get_discover_feed(cursor=first_page.next_cursor, limit=2)

        first_ids = {post.id for post in first_page.posts}
        second_ids = {post.id for post in second_page.posts}

        assert first_page.next_cursor
        assert FeedCursor.decode(first_page.next_cursor)["score"] is not None
        assert first_ids.isdisjoint(second_ids)

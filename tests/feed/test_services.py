"""Tests for FeedService — For You, Following, and Discover feeds."""

import pytest

from core.feed.services import FeedService


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


class TestDiscoverFeed:
    """Tests for the Discover feed."""

    def test_discover_returns_posts(self, user_a, user_b, posts):
        result = FeedService.get_discover_feed(str(user_b.id))
        assert len(result.posts) > 0

    def test_discover_includes_own(self, user_a, posts):
        result = FeedService.get_discover_feed(str(user_a.id))
        own_posts_present = any(p.author.id == str(user_a.id) for p in result.posts)
        assert own_posts_present, "Expected own posts to be included per user request"

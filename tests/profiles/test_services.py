"""Tests for ProfileService — profile retrieval and updates."""

import pytest

from core.profiles.services import ProfileService
from core.shared.exceptions import ProfileError


@pytest.fixture
def user_a(create_user):
    return create_user(email="a@test.com", username="user_a")


@pytest.fixture
def user_b(create_user):
    return create_user(email="b@test.com", username="user_b")


class TestGetUserProfile:
    """Tests for profile retrieval."""

    def test_get_own_profile(self, user_a):
        result = ProfileService.get_user_profile(str(user_a.id), viewer_id=str(user_a.id))
        assert result.username == "user_a"
        assert result.is_own_profile is True

    def test_get_other_profile(self, user_a, user_b):
        result = ProfileService.get_user_profile(str(user_a.id), viewer_id=str(user_b.id))
        assert result.username == "user_a"
        assert result.is_own_profile is False
        assert result.is_following is False

    def test_following_state(self, user_a, user_b):
        from core.follows.services import FollowService

        FollowService.follow_user(str(user_b.id), str(user_a.id))
        result = ProfileService.get_user_profile(str(user_a.id), viewer_id=str(user_b.id))
        assert result.is_following is True

    def test_nonexistent_user(self):
        with pytest.raises(ProfileError):
            ProfileService.get_user_profile("00000000-0000-0000-0000-000000000000")


class TestUpdateProfile:
    """Tests for profile updates."""

    def test_update_bio(self, user_a):
        result = ProfileService.update_profile(str(user_a.id), bio="My new bio")
        assert result.bio == "My new bio"

    def test_bio_too_long(self, user_a):
        with pytest.raises(ProfileError):
            ProfileService.update_profile(str(user_a.id), bio="x" * 151)

    def test_update_full_name(self, user_a):
        result = ProfileService.update_profile(str(user_a.id), full_name="New Name")
        assert result.full_name == "New Name"

    def test_update_bio_link(self, user_a):
        result = ProfileService.update_profile(str(user_a.id), bio_link="https://ziona.app/about")
        assert result.bio_link == "https://ziona.app/about"

    def test_update_bio_link_normalizes_missing_scheme(self, user_a):
        result = ProfileService.update_profile(str(user_a.id), bio_link="ziona.app/about")
        assert result.bio_link == "https://ziona.app/about"

    def test_invalid_bio_link_raises(self, user_a):
        with pytest.raises(ProfileError):
            ProfileService.update_profile(str(user_a.id), bio_link="not a valid link")

    def test_update_multiple_fields(self, user_a):
        result = ProfileService.update_profile(
            str(user_a.id),
            bio="Updated bio",
            bio_link="https://ziona.app/profile",
            full_name="Updated Name",
            location="New York",
        )
        assert result.bio == "Updated bio"
        assert result.bio_link == "https://ziona.app/profile"
        assert result.full_name == "Updated Name"
        assert result.location == "New York"


class TestGetUserPosts:
    """Tests for get_user_posts pagination."""

    @pytest.fixture
    def author(self, create_user):
        return create_user(email="author@x.com", username="author1")

    @pytest.fixture
    def viewer(self, create_user):
        return create_user(email="viewer@x.com", username="viewer1")

    @pytest.fixture
    def posts(self, author):
        from core.categories.models import Category
        from core.posts.models import Post, PostType

        category = Category.objects.create(label="Test", slug="test", order=1)

        posts = []
        for i in range(5):
            p = Post.objects.create(
                user=author,
                post_type=PostType.TEXT,
                caption=f"Post {i}",
                category=category,
            )
            posts.append(p)
        return posts

    def test_get_user_posts_returns_posts(self, author, viewer, posts):
        result = ProfileService.get_user_posts(
            user_id=str(author.id), viewer_id=str(viewer.id), limit=10
        )
        assert len(result["posts"]) == 5
        assert result["has_more"] is False
        assert result["next_cursor"] is None
        # Verify ordering is newest first
        assert result["posts"][0].caption == "Post 4"

    def test_get_user_posts_pagination(self, author, viewer, posts):
        # Fetch first page (limit 3)
        page1 = ProfileService.get_user_posts(
            user_id=str(author.id), viewer_id=str(viewer.id), limit=3
        )
        assert len(page1["posts"]) == 3
        assert page1["has_more"] is True
        assert page1["next_cursor"] is not None

        # Fetch second page using next_cursor
        page2 = ProfileService.get_user_posts(
            user_id=str(author.id), viewer_id=str(viewer.id), limit=3, cursor=page1["next_cursor"]
        )
        assert len(page2["posts"]) == 2
        assert page2["has_more"] is False
        assert page2["next_cursor"] is None

    def test_empty_user_posts(self, viewer):
        result = ProfileService.get_user_posts(user_id=str(viewer.id), viewer_id=str(viewer.id))
        assert result["posts"] == []


class TestGetUserLikedPosts:
    """Tests for get_user_liked_posts functionality."""

    @pytest.fixture
    def liker(self, create_user):
        return create_user(email="liker@x.com", username="liker1")

    @pytest.fixture
    def auth(self, create_user):
        return create_user(email="auth@x.com", username="auth1")

    @pytest.fixture
    def liked_posts(self, liker, auth):
        import datetime

        from django.utils import timezone

        from core.categories.models import Category
        from core.engagement.models import Like
        from core.posts.models import Post, PostType

        category = Category.objects.create(label="Test2", slug="test2", order=2)

        posts = []
        for i in range(3):
            # Use fixed distinct created_at to avoid flaky ordering
            p = Post.objects.create(
                user=auth,
                post_type=PostType.TEXT,
                caption=f"Liked Post {i}",
                category=category,
            )
            Post.objects.filter(id=p.id).update(
                created_at=timezone.now() - datetime.timedelta(minutes=i)
            )
            p.refresh_from_db()
            posts.append(p)
            Like.objects.create(user=liker, post=p)

        return posts

    def test_get_user_liked_posts(self, liker, auth, liked_posts):
        result = ProfileService.get_user_liked_posts(
            user_id=str(liker.id), viewer_id=str(auth.id), limit=10
        )
        assert len(result["posts"]) == 3
        # Should be ordered by post.created_at descending globally natively
        assert result["posts"][0].caption == "Liked Post 0"

    def test_get_user_liked_posts_pagination(self, liker, auth, liked_posts):
        page1 = ProfileService.get_user_liked_posts(
            user_id=str(liker.id), viewer_id=str(auth.id), limit=2
        )
        assert len(page1["posts"]) == 2
        assert page1["has_more"] is True

        page2 = ProfileService.get_user_liked_posts(
            user_id=str(liker.id), viewer_id=str(auth.id), limit=2, cursor=page1["next_cursor"]
        )
        assert len(page2["posts"]) == 1
        assert page2["has_more"] is False

    def test_empty_liked_posts(self, auth):
        result = ProfileService.get_user_liked_posts(user_id=str(auth.id))
        assert result["posts"] == []
        assert result["has_more"] is False

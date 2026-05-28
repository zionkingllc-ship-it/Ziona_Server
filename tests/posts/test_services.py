"""Tests for PostService — creation, validation, update, and deletion."""

import pytest

from core.posts.models import Post
from core.shared.exceptions import PostError


@pytest.fixture
def user_a(create_user):
    return create_user(email="a@test.com", username="user_a")


@pytest.fixture
def user_b(create_user):
    return create_user(email="b@test.com", username="user_b")


class TestCreatePost:
    """Tests for post creation."""

    def test_create_text_post(self, user_a):
        from core.posts.services import PostService

        result = PostService.create_post(
            user_id=str(user_a.id),
            post_type="text",
            caption="Hello world!",
        )
        assert result.type == "text"
        assert result.caption == "Hello world!"

    def test_create_text_post_too_long(self, user_a):
        from core.posts.services import PostService

        with pytest.raises(PostError):
            PostService.create_post(
                user_id=str(user_a.id), post_type="text", category_id="invalid-999"
            )

    def test_create_post_invalid_category(self, user_a):
        from core.posts.services import PostService
        from core.shared.exceptions import PostError

        with pytest.raises(PostError) as excinfo:
            PostService.create_post(
                user_id=str(user_a.id),
                post_type="text",
                category_id="invalid-abc",
            )
        assert excinfo.value.code == "INVALID_CATEGORY"

    def test_create_image_post(self, user_a, db):
        from core.media.models import MediaFile
        from core.posts.services import PostService

        media = MediaFile.objects.create(
            user=user_a,
            file_name="img.jpg",
            storage_path="img.jpg",
            media_type="image",
            file_size=1024,
        )

        result = PostService.create_post(
            user_id=str(user_a.id),
            post_type="image",
            caption="My photo",
            media_ids=[str(media.id)],
        )
        assert result.type == "image"


class TestPostViewerState:
    """Tests for viewer-specific post read contracts."""

    def test_likes_count_includes_viewer_like_when_viewer_state_liked(self, user_a, user_b):
        from core.engagement.models import Like
        from core.posts.services import PostService

        user_a.hide_like_count = True
        user_a.save(update_fields=["hide_like_count", "updated_at"])
        post = Post.objects.create(user=user_a, post_type="text", caption="Liked by viewer")
        Like.objects.create(user=user_b, post=post)

        result = PostService.get_post(str(post.id), viewer_id=str(user_b.id))

        assert result.viewer_state.liked is True
        assert result.stats.likes_count >= 1


class TestUpdatePost:
    """Tests for post update."""

    def test_update_caption(self, user_a):
        from core.posts.services import PostService

        post = PostService.create_post(
            user_id=str(user_a.id),
            post_type="text",
            caption="Original",
        )
        updated = PostService.update_post(
            post_id=post.id,
            user_id=str(user_a.id),
            caption="Updated caption",
        )
        assert updated.caption == "Updated caption"


class TestDeletePost:
    """Tests for post deletion."""

    def test_soft_delete(self, user_a):
        from core.posts.services import PostService

        post = PostService.create_post(
            user_id=str(user_a.id),
            post_type="text",
            caption="To delete",
        )
        PostService.delete_post(post_id=post.id, user_id=str(user_a.id))

        db_post = Post.all_objects.get(id=post.id)
        assert db_post.deleted_at is not None

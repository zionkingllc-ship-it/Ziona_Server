"""Tests for PostService — creation, validation, update, and deletion."""

import pytest

from core.posts.models import Post
from core.shared.exceptions import PostError


@pytest.fixture
def user_a(create_user):
    return create_user(email="a@test.com", username="user_a")


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
                user_id=str(user_a.id),
                post_type="text",
                caption="x" * 501,
            )

    def test_create_image_post(self, user_a):
        from core.posts.services import PostService

        result = PostService.create_post(
            user_id=str(user_a.id),
            post_type="image",
            caption="My photo",
            media_items=[
                {
                    "media_url": "https://example.com/img.jpg",
                    "media_type": "image",
                    "width": 1080,
                    "height": 1080,
                    "order": 0,
                }
            ],
        )
        assert result.type == "image"


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

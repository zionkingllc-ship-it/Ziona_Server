"""Tests for EngagementService — likes, comments, saves."""

from unittest.mock import patch

import pytest
from django.test import override_settings

from core.engagement.services import EngagementService
from core.shared.exceptions import EngagementError


@pytest.fixture
def user_a(create_user):
    return create_user(email="a@test.com", username="user_a")


@pytest.fixture
def user_b(create_user):
    return create_user(email="b@test.com", username="user_b")


@pytest.fixture
def post(user_a):
    from core.posts.models import Post

    return Post.objects.create(
        user=user_a,
        post_type="text",
        caption="Test post",
    )


class TestLikePost:
    """Tests for like/unlike operations."""

    @patch("core.engagement.services.check_engagement_spam")
    def test_like_post_success(self, mock_spam, user_b, post):
        result = EngagementService.like_post(str(user_b.id), str(post.id))
        assert result.success is True
        assert result.liked is True

    @patch("core.engagement.services.check_engagement_spam")
    def test_like_post_already_liked(self, mock_spam, user_b, post):
        EngagementService.like_post(str(user_b.id), str(post.id))

        result = EngagementService.like_post(str(user_b.id), str(post.id))
        assert result.success is True
        assert result.liked is True

    @patch("core.engagement.services.check_engagement_spam")
    def test_ensure_post_liked_is_idempotent(self, mock_spam, user_b, post):
        first = EngagementService.ensure_post_liked(str(user_b.id), str(post.id))
        second = EngagementService.ensure_post_liked(str(user_b.id), str(post.id))

        assert first.success is True
        assert second.success is True
        assert first.liked is True
        assert second.liked is True
        assert post.likes.count() == 1

    @patch("core.engagement.services.check_engagement_spam")
    def test_like_nonexistent_post(self, mock_spam, user_b):
        with pytest.raises(EngagementError) as exc:
            EngagementService.like_post(str(user_b.id), "00000000-0000-0000-0000-000000000000")
        assert exc.value.code == "POST_NOT_FOUND"

    @patch("core.engagement.services.check_engagement_spam")
    def test_unlike_post(self, mock_spam, user_b, post):
        EngagementService.like_post(str(user_b.id), str(post.id))
        result = EngagementService.unlike_post(str(user_b.id), str(post.id))
        assert result.success is True
        assert result.liked is False


class TestCreateComment:
    """Tests for comment creation."""

    def test_create_comment_success(self, user_b, post):
        result = EngagementService.create_comment(
            user_id=str(user_b.id),
            post_id=str(post.id),
            text="Great post!",
        )
        assert result.text == "Great post!"
        assert result.post_id == str(post.id)

    def test_create_comment_empty_text(self, user_b, post):
        with pytest.raises(EngagementError):
            EngagementService.create_comment(
                user_id=str(user_b.id),
                post_id=str(post.id),
                text="",
            )

    def test_create_comment_too_long(self, user_b, post):
        with pytest.raises(EngagementError) as exc:
            EngagementService.create_comment(
                user_id=str(user_b.id),
                post_id=str(post.id),
                text="x" * 501,
            )
        assert exc.value.code == "COMMENT_TOO_LONG"

    def test_create_threaded_reply(self, user_a, user_b, post):
        parent = EngagementService.create_comment(
            user_id=str(user_a.id),
            post_id=str(post.id),
            text="Parent comment",
        )
        reply = EngagementService.create_comment(
            user_id=str(user_b.id),
            post_id=str(post.id),
            text="Reply to parent",
            parent_comment_id=parent.id,
        )
        assert reply.parent_comment_id == parent.id


class TestDeleteComment:
    """Tests for comment deletion."""

    def test_delete_own_comment(self, user_b, post):
        comment = EngagementService.create_comment(
            user_id=str(user_b.id),
            post_id=str(post.id),
            text="My comment",
        )
        result = EngagementService.delete_comment(str(user_b.id), comment.id)
        assert result.success is True
        assert result.post_id == str(post.id)

    def test_delete_other_users_comment(self, user_a, user_b, post):
        comment = EngagementService.create_comment(
            user_id=str(user_a.id),
            post_id=str(post.id),
            text="A's comment",
        )
        with pytest.raises(EngagementError) as exc:
            EngagementService.delete_comment(str(user_b.id), comment.id)
        assert exc.value.code == "PERMISSION_DENIED"


class TestLikeComment:
    """Tests for liking comments returning fresh stats (Bugs 6 & 10)."""

    def test_like_comment_returns_updated_stats(self, user_a, user_b, post):
        comment = EngagementService.create_comment(
            user_id=str(user_a.id),
            post_id=str(post.id),
            text="Parent comment",
        )
        # A reply so replies_count is non-zero in the returned stats.
        EngagementService.create_comment(
            user_id=str(user_b.id),
            post_id=str(post.id),
            text="A reply",
            parent_comment_id=comment.id,
        )

        stats = EngagementService.like_comment(str(user_b.id), str(comment.id))
        assert stats.likes_count == 1
        assert stats.replies_count == 1

    def test_like_comment_is_idempotent(self, user_b, post):
        comment = EngagementService.create_comment(
            user_id=str(user_b.id),
            post_id=str(post.id),
            text="A comment",
        )
        EngagementService.like_comment(str(user_b.id), str(comment.id))
        stats = EngagementService.like_comment(str(user_b.id), str(comment.id))
        assert stats.likes_count == 1

    def test_like_nonexistent_comment(self, user_b):
        with pytest.raises(EngagementError) as exc:
            EngagementService.like_comment(str(user_b.id), "00000000-0000-0000-0000-000000000000")
        assert exc.value.code == "COMMENT_NOT_FOUND"


class TestSavePost:
    """Tests for save/unsave operations."""

    def test_save_post_success(self, user_b, post):
        result = EngagementService.save_post(str(user_b.id), str(post.id))
        assert result.success is True
        assert result.saved is True

    def test_save_post_already_saved(self, user_b, post):
        EngagementService.save_post(str(user_b.id), str(post.id))
        with pytest.raises(EngagementError) as exc:
            EngagementService.save_post(str(user_b.id), str(post.id))
        assert exc.value.code == "ALREADY_SAVED"

    def test_unsave_post(self, user_b, post):
        EngagementService.save_post(str(user_b.id), str(post.id))
        result = EngagementService.unsave_post(str(user_b.id), str(post.id))
        assert result.success is True
        assert result.saved is False


class TestSharePost:
    """Tests for share URL generation."""

    @override_settings(APP_SHARE_BASE_URL="https://share.ziona.test")
    def test_share_post_external_uses_configured_base_url(self, user_a, user_b):
        from core.engagement.share_services import ShareService
        from core.posts.models import Post

        db_post = Post.objects.create(
            user=user_a,
            post_type="text",
            caption="Share me",
        )

        result = ShareService.share_post_external(str(user_b.id), str(db_post.id))

        assert result.success is True
        assert result.share_url == f"https://share.ziona.test/post/{db_post.id}"


class TestGetPostComments:
    """Tests for paginated comment retrieval."""

    def test_get_empty_comments(self, post):
        result = EngagementService.get_post_comments(str(post.id))
        assert len(result.comments) == 0
        assert result.has_more is False

    def test_get_comments_with_viewer(self, user_a, user_b, post):
        EngagementService.create_comment(
            user_id=str(user_a.id),
            post_id=str(post.id),
            text="First!",
        )
        result = EngagementService.get_post_comments(str(post.id), viewer_id=str(user_b.id))
        assert len(result.comments) == 1
        assert result.comments[0].viewer_state is not None

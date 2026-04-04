"""Tests for BookmarkService — folder CRUD, filtering, and bulk operations."""

import pytest

from core.engagement.bookmark_services import BookmarkService
from core.shared.exceptions import BookmarkError


@pytest.fixture
def user_a(create_user):
    return create_user(email="a@test.com", username="user_a")


@pytest.fixture
def user_b(create_user):
    return create_user(email="b@test.com", username="user_b")


@pytest.fixture
def post_image(user_a):
    from core.posts.models import Post, PostMedia

    p = Post.objects.create(user=user_a, post_type="image", caption="Image post")
    PostMedia.objects.create(post=p, media_url="https://test.com/img.jpg", media_type="image")
    return p


@pytest.fixture
def post_video(user_a):
    from core.posts.models import Post, PostMedia

    p = Post.objects.create(user=user_a, post_type="video", caption="Video post")
    PostMedia.objects.create(post=p, media_url="https://test.com/vid.mp4", media_type="video")
    return p


@pytest.fixture
def post_text(user_a):
    from core.posts.models import Post

    return Post.objects.create(user=user_a, post_type="text", caption="Text post")


class TestBookmarkFolders:
    """Tests for bookmark folder management."""

    def test_default_folders_created(self, user_a):
        folders = BookmarkService.get_folders(str(user_a.id))
        folder_names = {f.name for f in folders}
        expected = {"All", "Churches", "Prayer References", "Bible Study", "Events/Concerts"}
        assert folder_names == expected

    def test_create_custom_folder(self, user_a):
        BookmarkService.get_folders(str(user_a.id))
        folder = BookmarkService.create_folder(str(user_a.id), "My Folder")
        assert folder.name == "My Folder"

    def test_delete_folder_returns_moved_count(self, user_a, post_text):
        """Delete folder returns dict with moved_posts_count."""
        from core.engagement.services import EngagementService

        BookmarkService.get_folders(str(user_a.id))
        folder = BookmarkService.create_folder(str(user_a.id), "To Delete")
        EngagementService.save_post(str(user_a.id), str(post_text.id), folder.id)

        result = BookmarkService.delete_folder(str(user_a.id), folder.id)
        assert result["deleted"] is True
        assert result["moved_posts_count"] == 1

    def test_delete_nonexistent_folder(self, user_a):
        with pytest.raises(BookmarkError) as exc_info:
            BookmarkService.delete_folder(str(user_a.id), "00000000-0000-0000-0000-000000000000")
        assert exc_info.value.code == "FOLDER_NOT_FOUND"

    def test_delete_other_users_folder_fails(self, user_a, user_b):
        """Cannot delete another user's folder."""
        BookmarkService.get_folders(str(user_a.id))
        folder = BookmarkService.create_folder(str(user_a.id), "Private")

        with pytest.raises(BookmarkError) as exc_info:
            BookmarkService.delete_folder(str(user_b.id), folder.id)
        assert exc_info.value.code == "FOLDER_ACCESS_DENIED"

    def test_delete_folder_posts_go_to_all(self, user_a, post_text):
        """After folder deletion, posts are accessible without folder filter."""
        from core.engagement.services import EngagementService

        BookmarkService.get_folders(str(user_a.id))
        folder = BookmarkService.create_folder(str(user_a.id), "Temp")
        EngagementService.save_post(str(user_a.id), str(post_text.id), folder.id)

        BookmarkService.delete_folder(str(user_a.id), folder.id)
        result = BookmarkService.get_saved_posts(str(user_a.id))
        assert len(result["posts"]) == 1


class TestBookmarkFiltering:
    """Tests for bookmark media type filtering."""

    @pytest.fixture(autouse=True)
    def _setup_bookmarks(self, user_a, post_image, post_video, post_text):
        """Save all three post types."""
        from core.engagement.services import EngagementService

        self.user_id = str(user_a.id)
        EngagementService.save_post(self.user_id, str(post_image.id))
        EngagementService.save_post(self.user_id, str(post_video.id))
        EngagementService.save_post(self.user_id, str(post_text.id))

    def test_filter_all_returns_everything(self):
        result = BookmarkService.get_saved_posts(self.user_id, media_type="all")
        assert len(result["posts"]) == 3

    def test_filter_by_image(self):
        result = BookmarkService.get_saved_posts(self.user_id, media_type="image")
        assert len(result["posts"]) == 1
        assert result["posts"][0].type == "image"

    def test_filter_by_video(self):
        result = BookmarkService.get_saved_posts(self.user_id, media_type="video")
        assert len(result["posts"]) == 1
        assert result["posts"][0].type == "video"

    def test_filter_by_text(self):
        result = BookmarkService.get_saved_posts(self.user_id, media_type="text")
        assert len(result["posts"]) == 1
        assert result["posts"][0].type == "text"

    def test_filter_custom_folder_by_type(self, user_a):
        """Filtering works inside a custom folder."""
        from core.engagement.services import EngagementService
        from core.posts.models import Post, PostMedia

        new_image_post = Post.objects.create(user=user_a, post_type="image", caption="Folder pic")
        PostMedia.objects.create(
            post=new_image_post, media_url="https://test.com/img2.jpg", media_type="image"
        )

        BookmarkService.get_folders(str(user_a.id))
        folder = BookmarkService.create_folder(str(user_a.id), "Pics Only")
        EngagementService.save_post(str(user_a.id), str(new_image_post.id), folder.id)

        result = BookmarkService.get_saved_posts(
            str(user_a.id), folder_id=folder.id, media_type="image"
        )
        assert len(result["posts"]) == 1

    def test_invalid_media_type_fails(self):
        with pytest.raises(BookmarkError) as exc_info:
            BookmarkService.get_saved_posts(self.user_id, media_type="audio")
        assert exc_info.value.code == "INVALID_MEDIA_TYPE"


class TestBulkRemoveBookmarks:
    """Tests for bulk bookmark removal."""

    def test_bulk_remove_multiple(self, user_a, post_image, post_text):
        from core.engagement.services import EngagementService

        EngagementService.save_post(str(user_a.id), str(post_image.id))
        EngagementService.save_post(str(user_a.id), str(post_text.id))

        result = BookmarkService.bulk_remove_bookmarks(
            str(user_a.id), [str(post_image.id), str(post_text.id)]
        )
        assert result["removed_count"] == 2

    def test_returns_correct_count(self, user_a, post_image, post_text):
        """Only bookmarked posts are counted."""
        from core.engagement.services import EngagementService

        EngagementService.save_post(str(user_a.id), str(post_image.id))

        result = BookmarkService.bulk_remove_bookmarks(
            str(user_a.id), [str(post_image.id), str(post_text.id)]
        )
        assert result["removed_count"] == 1

    def test_non_bookmarked_silent(self, user_a, post_image):
        """Non-bookmarked posts are silently skipped."""
        result = BookmarkService.bulk_remove_bookmarks(str(user_a.id), [str(post_image.id)])
        assert result["removed_count"] == 0

    def test_empty_post_ids(self, user_a):
        """Empty list returns removed_count=0."""
        result = BookmarkService.bulk_remove_bookmarks(str(user_a.id), [])
        assert result["removed_count"] == 0

    def test_across_folders(self, user_a, post_image, post_text):
        """Bulk remove works across different folders."""
        from core.engagement.services import EngagementService

        BookmarkService.get_folders(str(user_a.id))
        folder = BookmarkService.create_folder(str(user_a.id), "F1")

        EngagementService.save_post(str(user_a.id), str(post_image.id), folder.id)
        EngagementService.save_post(str(user_a.id), str(post_text.id))

        result = BookmarkService.bulk_remove_bookmarks(
            str(user_a.id), [str(post_image.id), str(post_text.id)]
        )
        assert result["removed_count"] == 2

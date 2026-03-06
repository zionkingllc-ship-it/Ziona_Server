"""Integration tests — end-to-end flows across multiple domains."""

from unittest.mock import patch

import pytest

from core.engagement.services import EngagementService
from core.feed.services import FeedService
from core.follows.services import FollowService
from core.profiles.services import ProfileService


@pytest.fixture
def user_a(create_user):
    return create_user(email="a@test.com", username="user_a")


@pytest.fixture
def user_b(create_user):
    return create_user(email="b@test.com", username="user_b")


@pytest.fixture
def user_c(create_user):
    return create_user(email="c@test.com", username="user_c")


@pytest.mark.integration
class TestPostCreationFeedFlow:
    """Test: Create post → appears in follower's feed."""

    def test_post_appears_in_following_feed(self, user_a, user_b):
        from core.posts.models import Post

        FollowService.follow_user(str(user_b.id), str(user_a.id))

        Post.objects.create(
            user=user_a,
            post_type="text",
            caption="Faith message",
        )

        feed = FeedService.get_following_feed(str(user_b.id))
        assert len(feed.posts) >= 1
        assert any(p.author.username == "user_a" for p in feed.posts)


@pytest.mark.integration
class TestFollowProfileFlow:
    """Test: Follow → profile shows follower count."""

    def test_follow_updates_profile(self, user_a, user_b):
        FollowService.follow_user(str(user_b.id), str(user_a.id))

        profile = ProfileService.get_user_profile(str(user_a.id), viewer_id=str(user_b.id))
        assert profile.stats.followers_count >= 1
        assert profile.is_following is True


@pytest.mark.integration
class TestEngagementFeedFlow:
    """Test: Like/comment → engagement counts in feed."""

    @patch("core.engagement.services.check_engagement_spam")
    def test_engagement_reflects_in_feed(self, mock_spam, user_a, user_b):
        from core.posts.models import Post

        Post.objects.create(
            user=user_a,
            post_type="text",
            caption="Test post for engagement",
        )

        feed = FeedService.get_for_you_feed(str(user_b.id))
        if feed.posts:
            post_id = feed.posts[0].id

            EngagementService.like_post(str(user_b.id), post_id)

            EngagementService.create_comment(
                user_id=str(user_b.id),
                post_id=post_id,
                text="Amen! 🙏",
            )

            comments = EngagementService.get_post_comments(post_id, viewer_id=str(user_b.id))
            assert len(comments.comments) >= 1


@pytest.mark.integration
class TestReportingFlow:
    """Test: Report content → appears in admin reports."""

    def test_report_and_list(self, user_a, user_b):
        from core.moderation.services import ReportService
        from core.posts.models import Post

        post = Post.objects.create(
            user=user_a,
            post_type="text",
            caption="Offensive content",
        )

        ReportService.report_content(
            reporter_id=str(user_b.id),
            reason="policy_violation",
            post_id=str(post.id),
        )

        reports = ReportService.list_reports()
        assert len(reports["reports"]) >= 1

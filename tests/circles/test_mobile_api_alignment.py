import json
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.test import Client, TestCase
from django.utils import timezone

from core.circles.anchor_services import create_anchor, get_anchor_history
from core.circles.models import Anchor, Circle, CircleMembership, CirclePost, CircleRule


def _make_user(email, username=None, full_name="", avatar_url=""):
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    user = user_model.objects.create_user(email=email, password="testpass123")
    user.username = username
    user.full_name = full_name
    user.avatar_url = avatar_url
    user.save(update_fields=["username", "full_name", "avatar_url"])
    return user


def _graphql(query, variables=None, access_token=None):
    request_kwargs = {}
    if access_token:
        request_kwargs["HTTP_AUTHORIZATION"] = f"Bearer {access_token}"

    response = Client().post(
        "/graphql/",
        data=json.dumps({"query": query, "variables": variables or {}}),
        content_type="application/json",
        **request_kwargs,
    )
    content = json.loads(response.content)
    assert response.status_code == 200, content
    assert "errors" not in content, content.get("errors")
    return content["data"]


@pytest.mark.django_db
class TestMobileAnchorAlignment(TestCase):
    def setUp(self):
        self.admin = _make_user("mobile-admin@test.com", "mobile_admin")
        self.circle = Circle.objects.create(
            name="Mobile Circle",
            description="A mobile-aligned circle",
            cover_image="https://example.com/circle.jpg",
            created_by=self.admin,
        )
        CircleMembership.objects.create(circle=self.circle, user=self.admin, role="admin")

    def test_create_anchor_accepts_mobile_text_type_with_visual_fields(self):
        anchor = create_anchor(
            creator_id=str(self.admin.id),
            circle_id=str(self.circle.id),
            anchor_type="text",
            title="Mobile Text Anchor",
            content="Work as unto the Lord",
            scripture_book="Colossians",
            scripture_chapter=3,
            scripture_verse_start=23,
            scripture_text="Whatever you do, work at it with all your heart.",
            anchor_text="Lord, help me honor you in my work.",
            background_colors=["#A8D5A2", "#EDEDED"],
        )

        self.assertEqual(anchor.anchor_type, "text")
        self.assertEqual(anchor.anchor_text, "Lord, help me honor you in my work.")
        self.assertEqual(anchor.background_colors, ["#A8D5A2", "#EDEDED"])

    def test_create_anchor_accepts_mobile_image_text_type(self):
        anchor = create_anchor(
            creator_id=str(self.admin.id),
            circle_id=str(self.circle.id),
            anchor_type="image_text",
            title="Mobile Image Text Anchor",
            anchor_image="https://example.com/anchor.jpg",
            anchor_image_text="Let everything that has breath praise the Lord.",
        )

        self.assertEqual(anchor.anchor_type, "image_text")
        self.assertEqual(anchor.anchor_image, "https://example.com/anchor.jpg")
        self.assertEqual(
            anchor.anchor_image_text, "Let everything that has breath praise the Lord."
        )

    def test_legacy_anchor_types_still_work(self):
        anchor = create_anchor(
            creator_id=str(self.admin.id),
            circle_id=str(self.circle.id),
            anchor_type="bible_verse",
            title="Legacy Bible Anchor",
            scripture_book="John",
            scripture_chapter=3,
            scripture_verse_start=16,
            scripture_text="For God so loved the world.",
        )

        self.assertEqual(anchor.anchor_type, "bible_verse")
        self.assertEqual(anchor.scripture_book, "John")

    def test_anchor_history_can_exclude_active_anchor(self):
        now = timezone.now()
        active = Anchor.objects.create(
            circle=self.circle,
            created_by=self.admin,
            anchor_type="text",
            title="Active",
            published_at=now - timedelta(hours=1),
            expires_at=now + timedelta(hours=23),
        )
        past = Anchor.objects.create(
            circle=self.circle,
            created_by=self.admin,
            anchor_type="text",
            title="Past",
            published_at=now - timedelta(days=2),
            expires_at=now - timedelta(days=1),
        )

        all_anchors = get_anchor_history(str(self.circle.id))
        past_anchors = get_anchor_history(str(self.circle.id), include_active=False)

        self.assertIn(active, all_anchors)
        self.assertIn(past, all_anchors)
        self.assertEqual(past_anchors, [past])


@pytest.mark.django_db
class TestMobileGraphQLAlignment(TestCase):
    def setUp(self):
        now = timezone.now()
        self.author = _make_user(
            "post-author@test.com",
            "post_author",
            "Post Author",
            "https://example.com/avatar.jpg",
        )
        self.viewer = _make_user(
            "viewer@test.com",
            "viewer_user",
            "Viewer User",
            "https://example.com/viewer.jpg",
        )
        from core.authentication.tokens import TokenService

        self.viewer_access_token = TokenService.generate_access_token(
            str(self.viewer.id), self.viewer.role
        )
        self.circle = Circle.objects.create(
            name="Faith, Work & Purpose",
            description="A community where Christians discuss career and purpose.",
            cover_image="https://example.com/cover.jpg",
            banner_image="https://example.com/banner.jpg",
            profile_image_url="https://example.com/profile.jpg",
            created_by=self.author,
        )
        CircleMembership.objects.create(circle=self.circle, user=self.author, role="admin")
        CircleRule.objects.create(
            circle=self.circle,
            rule_number=1,
            title="Honor God",
            description="Let your work reflect your faith.",
            is_default=False,
        )
        self.anchor = Anchor.objects.create(
            circle=self.circle,
            created_by=self.author,
            anchor_type="text",
            title="Reflection of the Week",
            content="Work as unto the Lord",
            scripture_book="Colossians",
            scripture_chapter=3,
            scripture_verse_start=23,
            scripture_text="Whatever you do, work at it with all your heart.",
            anchor_text="Lord, help me see my work as worship.",
            background_colors=["#A8D5A2", "#EDEDED"],
            prayed_count=62,
            anchor_liked_count=234,
            published_at=now - timedelta(hours=1),
            expires_at=now + timedelta(hours=23),
        )
        self.past_anchor = Anchor.objects.create(
            circle=self.circle,
            created_by=self.author,
            anchor_type="image_text",
            title="Yesterday's Prayer",
            anchor_image_text="Father, grant me wisdom for today.",
            published_at=now - timedelta(days=2),
            expires_at=now - timedelta(days=1),
        )
        self.post = CirclePost.objects.create(
            circle=self.circle,
            user=self.author,
            text="God is so good!",
            image_url="https://example.com/post.jpg",
            likes_count=24,
            comments_count=5,
            prayed_count=71,
            anchor_liked_count=18,
        )
        self.viewer_post = CirclePost.objects.create(
            circle=self.circle,
            user=self.viewer,
            text="This one is mine.",
            likes_count=3,
            comments_count=1,
            prayed_count=2,
            anchor_liked_count=1,
        )
        self.trending_post = CirclePost.objects.create(
            circle=self.circle,
            user=self.author,
            text="This one should trend first.",
            likes_count=80,
            comments_count=20,
            prayed_count=15,
            anchor_liked_count=10,
        )

    def test_circle_query_exposes_mobile_aliases_rules_and_anchor_fields(self):
        data = _graphql(
            """
            query Circle($id: String!) {
              circle(id: $id) {
                id
                name
                title
                coverImage
                suggestionCardImage
                image
                bannerImage
                profileImage
                memberCount
                members
                avatars
                isSubscribed
                isJoined
                rules { id ruleNumber title description }
                activeAnchor {
                  id
                  anchorType
                  type
                  date
                  anchorDate
                  bibleReference
                  bibleText
                  anchorText
                  backgroundColors
                  prayedCount
                  anchorLikedCount
                  author { id username avatarUrl }
                }
                anchorDates
              }
            }
            """,
            {"id": str(self.circle.id)},
        )

        circle = data["circle"]
        self.assertEqual(circle["title"], "Faith, Work & Purpose")
        self.assertEqual(circle["coverImage"], "https://example.com/cover.jpg")
        self.assertEqual(circle["suggestionCardImage"], "https://example.com/cover.jpg")
        self.assertEqual(circle["image"], "https://example.com/cover.jpg")
        self.assertEqual(circle["bannerImage"], "https://example.com/banner.jpg")
        self.assertEqual(circle["profileImage"], "https://example.com/profile.jpg")
        self.assertEqual(circle["members"], 1)
        self.assertEqual(circle["avatars"], ["https://example.com/avatar.jpg"])
        self.assertFalse(circle["isJoined"])
        self.assertEqual(circle["rules"][0]["id"], 1)
        self.assertEqual(circle["activeAnchor"]["anchorType"], "text")
        self.assertEqual(circle["activeAnchor"]["type"], "text")
        expected_anchor_date = self.anchor.published_at.date().isoformat()
        self.assertEqual(circle["activeAnchor"]["date"], expected_anchor_date)
        self.assertEqual(circle["activeAnchor"]["anchorDate"], expected_anchor_date)
        self.assertEqual(circle["activeAnchor"]["author"]["username"], "post_author")
        self.assertEqual(
            circle["activeAnchor"]["author"]["avatarUrl"], "https://example.com/avatar.jpg"
        )
        self.assertEqual(circle["anchorDates"][0], expected_anchor_date)
        self.assertEqual(circle["activeAnchor"]["bibleReference"], "Colossians 3:23")
        self.assertEqual(
            circle["activeAnchor"]["bibleText"],
            "Whatever you do, work at it with all your heart.",
        )

    def test_circle_feed_supports_explicit_circle_filter_values(self):
        data = _graphql(
            """
            query CircleFeed($circleId: String!, $filter: CirclePostFilterEnum!) {
              circleFeed(circleId: $circleId, circleFilter: $filter) {
                posts {
                  id
                  text
                  user { id }
                }
              }
            }
            """,
            {"circleId": str(self.circle.id), "filter": "TRENDING"},
            access_token=self.viewer_access_token,
        )

        posts = data["circleFeed"]["posts"]
        self.assertEqual(posts[0]["id"], str(self.trending_post.id))

    def test_circle_feed_supports_viewer_posts_without_author_workaround(self):
        data = _graphql(
            """
            query CircleFeed($circleId: String!, $filter: CirclePostFilterEnum!) {
              circleFeed(circleId: $circleId, circleFilter: $filter) {
                posts {
                  id
                  text
                  user { id }
                }
              }
            }
            """,
            {"circleId": str(self.circle.id), "filter": "VIEWER_POSTS"},
            access_token=self.viewer_access_token,
        )

        posts = data["circleFeed"]["posts"]
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["id"], str(self.viewer_post.id))
        self.assertEqual(posts[0]["user"]["id"], str(self.viewer.id))

    def test_circle_posts_alias_matches_circle_feed_and_post_aliases(self):
        data = _graphql(
            """
            query Feed($circleId: String!) {
              circleFeed(circleId: $circleId) {
                posts {
                  id
                  user { name avatar avatarUrl }
                  likes
                  likesCount
                  likeCount
                  comments
                  commentsCount
                  likedImage
                  prayedCount
                  anchorLikedCount
                  savedCount
                  sharedCount
                }
                pageInfo { totalCount hasNextPage currentPage }
              }
              circlePosts(circleId: $circleId) {
                posts { id likes comments user { avatar } }
                pageInfo { totalCount hasNextPage currentPage }
              }
            }
            """,
            {"circleId": str(self.circle.id)},
        )

        feed_post = next(
            post for post in data["circleFeed"]["posts"] if post["id"] == str(self.post.id)
        )
        alias_post = next(
            post for post in data["circlePosts"]["posts"] if post["id"] == str(self.post.id)
        )
        self.assertEqual(feed_post["id"], alias_post["id"])
        self.assertEqual(feed_post["likes"], 24)
        self.assertEqual(feed_post["likesCount"], 24)
        self.assertEqual(feed_post["likeCount"], 24)
        self.assertEqual(feed_post["comments"], 5)
        self.assertEqual(feed_post["commentsCount"], 5)
        self.assertEqual(feed_post["likedImage"], 1)
        self.assertEqual(feed_post["savedCount"], 0)
        self.assertEqual(feed_post["sharedCount"], 0)
        self.assertEqual(feed_post["user"]["avatar"], "https://example.com/avatar.jpg")
        self.assertEqual(data["circleFeed"]["pageInfo"]["totalCount"], 3)
        self.assertEqual(data["circlePosts"]["pageInfo"]["totalCount"], 3)

    def test_circle_feed_data_matches_mobile_shape(self):
        self.circle.display_member_count = 1247
        self.circle.save(update_fields=["display_member_count"])

        data = _graphql(
            """
            query FeedData($circleId: String!) {
              circleFeedData(circleId: $circleId) {
                bannerImage
                profileImage
                coverImage
                suggestionCardImage
                name
                description
                memberCount
                isJoined
                memberAvatars
                rules { id title description }
                anchorDates
                activeAnchor {
                  type
                  anchorDate
                  scripture
                  likedImage
                  anchorLikedCount
                  prayedCount
                }
                pastAnchors { title type anchorDate }
                posts { id likedImage savedCount sharedCount }
              }
            }
            """,
            {"circleId": str(self.circle.id)},
        )

        feed_data = data["circleFeedData"]
        self.assertEqual(feed_data["bannerImage"], "https://example.com/banner.jpg")
        self.assertEqual(feed_data["profileImage"], "https://example.com/profile.jpg")
        self.assertEqual(feed_data["coverImage"], "https://example.com/cover.jpg")
        self.assertEqual(feed_data["suggestionCardImage"], "https://example.com/cover.jpg")
        self.assertEqual(feed_data["memberCount"], 1247)
        self.assertFalse(feed_data["isJoined"])
        self.assertEqual(feed_data["memberAvatars"], ["https://example.com/avatar.jpg"])
        self.assertEqual(feed_data["activeAnchor"]["type"], "text")
        active_date = self.anchor.published_at.date().isoformat()
        past_date = self.past_anchor.published_at.date().isoformat()
        self.assertEqual(feed_data["activeAnchor"]["anchorDate"], active_date)
        self.assertEqual(feed_data["anchorDates"], [active_date, past_date])
        self.assertEqual(feed_data["activeAnchor"]["scripture"], "Colossians 3:23")
        self.assertEqual(feed_data["activeAnchor"]["likedImage"], 1)
        self.assertEqual(
            feed_data["pastAnchors"],
            [
                {
                    "title": "Yesterday's Prayer",
                    "type": "image_text",
                    "anchorDate": past_date,
                }
            ],
        )
        self.assertEqual(feed_data["posts"][0]["savedCount"], 0)
        self.assertEqual(feed_data["posts"][0]["sharedCount"], 0)

    def test_circle_feed_data_banner_falls_back_to_cover_image(self):
        self.circle.banner_image = ""
        self.circle.save(update_fields=["banner_image"])

        data = _graphql(
            """
            query FeedData($circleId: String!) {
              circleFeedData(circleId: $circleId) {
                bannerImage
                coverImage
                suggestionCardImage
              }
            }
            """,
            {"circleId": str(self.circle.id)},
        )

        feed_data = data["circleFeedData"]
        self.assertEqual(feed_data["bannerImage"], "https://example.com/cover.jpg")
        self.assertEqual(feed_data["coverImage"], "https://example.com/cover.jpg")
        self.assertEqual(feed_data["suggestionCardImage"], "https://example.com/cover.jpg")

    def test_anchor_history_include_active_false_returns_only_past_anchors(self):
        data = _graphql(
            """
            query History($circleId: String!) {
              anchorHistory(circleId: $circleId, includeActive: false) {
                title
                anchorType
                type
              }
            }
            """,
            {"circleId": str(self.circle.id)},
        )

        self.assertEqual(
            data["anchorHistory"],
            [{"title": "Yesterday's Prayer", "anchorType": "image_text", "type": "image_text"}],
        )


@pytest.mark.django_db
def test_seed_circle_sample_data_is_idempotent():
    call_command("seed_circle_sample_data")
    first_counts = (
        Circle.objects.filter(
            name__in=[circle["name"] for circle in call_command_circles()]
        ).count(),
        Anchor.objects.filter(circle__name="Faith, Work & Purpose").count(),
        CirclePost.objects.filter(user__email__endswith="@sample.ziona.app").count(),
    )

    call_command("seed_circle_sample_data")
    second_counts = (
        Circle.objects.filter(
            name__in=[circle["name"] for circle in call_command_circles()]
        ).count(),
        Anchor.objects.filter(circle__name="Faith, Work & Purpose").count(),
        CirclePost.objects.filter(user__email__endswith="@sample.ziona.app").count(),
    )

    assert first_counts == second_counts
    assert first_counts[0] == 7
    assert first_counts[1] == 8
    assert first_counts[2] == 11
    assert Circle.objects.get(name="Faith, Work & Purpose").display_member_count == 1247


def call_command_circles():
    from core.circles.management.commands.seed_circle_sample_data import CIRCLES

    return CIRCLES

import json
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.test import Client, TestCase
from django.test.utils import override_settings
from django.utils import timezone

from core.circles.anchor_services import create_anchor, get_anchor_history
from core.circles.models import Anchor, Circle, CircleMembership, CirclePost, CircleRule
from core.media.models import MediaFile, MediaStatus
from core.media.models import MediaType as StoredMediaType


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


def _make_media_file(
    *,
    user,
    storage_path,
    media_type=StoredMediaType.IMAGE,
    thumbnail_path="",
    width=1080,
    height=1080,
    duration=None,
    status=MediaStatus.READY,
):
    file_name = storage_path.split("/")[-1]
    file_type = "video/mp4" if media_type == StoredMediaType.VIDEO else "image/jpeg"
    return MediaFile.objects.create(
        user=user,
        file_name=file_name,
        file_type=file_type,
        file_size=0,
        media_type=media_type,
        storage_path=storage_path,
        thumbnail_path=thumbnail_path,
        width=width,
        height=height,
        duration=duration,
        status=status,
    )


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

    def test_circle_media_normalizes_fractional_video_duration(self):
        from core.circles.schema import _media_file_to_graphql

        media = _make_media_file(
            user=self.admin,
            storage_path="uploads/test/videos/fractional.mp4",
            media_type=StoredMediaType.VIDEO,
            duration=6.83,
        )

        self.assertEqual(_media_file_to_graphql(media).duration, 7)

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

        all_anchors = get_anchor_history(
            str(self.circle.id),
            viewer_id=str(self.admin.id),
        )
        past_anchors = get_anchor_history(
            str(self.circle.id),
            include_active=False,
            viewer_id=str(self.admin.id),
        )

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

        self.author_access_token = TokenService.generate_access_token(
            str(self.author.id), self.author.role
        )
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
        CircleMembership.objects.create(circle=self.circle, user=self.viewer, role="member")
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
        self.post_image_media = _make_media_file(
            user=self.author,
            storage_path="circle-posts/post-image.jpg",
            width=1440,
            height=1440,
        )
        self.trending_video_media = _make_media_file(
            user=self.author,
            storage_path="circle-posts/trending-video.mp4",
            media_type=StoredMediaType.VIDEO,
            thumbnail_path="circle-posts/trending-video-thumb.jpg",
            width=1920,
            height=1080,
            duration=24,
        )
        self.post = CirclePost.objects.create(
            circle=self.circle,
            user=self.author,
            text="God is so good!",
            likes_count=24,
            comments_count=5,
            prayed_count=71,
            anchor_liked_count=18,
        )
        self.post.media_files.set([self.post_image_media])
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
        self.trending_post.media_files.set([self.trending_video_media])

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
            access_token=self.viewer_access_token,
        )

        circle = data["circle"]
        self.assertEqual(circle["title"], "Faith, Work & Purpose")
        self.assertEqual(circle["coverImage"], "https://example.com/cover.jpg")
        self.assertEqual(circle["suggestionCardImage"], "https://example.com/cover.jpg")
        self.assertEqual(circle["image"], "https://example.com/cover.jpg")
        self.assertEqual(circle["bannerImage"], "https://example.com/banner.jpg")
        self.assertEqual(circle["profileImage"], "https://example.com/profile.jpg")
        self.assertEqual(circle["members"], 2)
        self.assertEqual(
            circle["avatars"],
            [
                "https://example.com/avatar.jpg",
                "https://example.com/viewer.jpg",
            ],
        )
        self.assertTrue(circle["isJoined"])
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
                  user { name username avatar avatarUrl }
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
                posts { id likes comments user { avatar username } }
                pageInfo { totalCount hasNextPage currentPage }
              }
            }
            """,
            {"circleId": str(self.circle.id)},
            access_token=self.viewer_access_token,
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
        self.assertEqual(feed_post["user"]["username"], "post_author")
        self.assertEqual(alias_post["user"]["username"], "post_author")
        self.assertEqual(data["circleFeed"]["pageInfo"]["totalCount"], 3)
        self.assertEqual(data["circlePosts"]["pageInfo"]["totalCount"], 3)

    def test_circle_post_queries_expose_unified_media_contract(self):
        data = _graphql(
            """
            query MediaContract($circleId: String!, $postId: String!) {
              circleFeed(circleId: $circleId) {
                posts {
                  id
                  media { id url thumbnailUrl type width height duration }
                  mediaUrl
                  mediaType
                  image { items { id url type width height } }
                  video { url thumbnailUrl duration width height }
                }
              }
              circlePosts(circleId: $circleId) {
                posts {
                  id
                  media { id url type }
                  mediaUrl
                  mediaType
                }
              }
              circlePost(id: $postId) {
                id
                media { id url thumbnailUrl type width height duration }
                mediaUrl
                mediaType
                image { items { id url type width height } }
                video { url thumbnailUrl duration width height }
              }
              circleFeedData(circleId: $circleId) {
                posts {
                  id
                  media { id url thumbnailUrl type width height duration }
                  mediaUrl
                  mediaType
                  image { items { id url type width height } }
                  video { url thumbnailUrl duration width height }
                }
              }
            }
            """,
            {"circleId": str(self.circle.id), "postId": str(self.post.id)},
            access_token=self.viewer_access_token,
        )

        feed_posts = {post["id"]: post for post in data["circleFeed"]["posts"]}
        alias_posts = {post["id"]: post for post in data["circlePosts"]["posts"]}
        feed_data_posts = {post["id"]: post for post in data["circleFeedData"]["posts"]}
        detail_post = data["circlePost"]

        image_post = feed_posts[str(self.post.id)]
        self.assertEqual(image_post["mediaUrl"], self.post_image_media.url)
        self.assertEqual(image_post["mediaType"], "image")
        self.assertEqual(image_post["media"][0]["type"], "IMAGE")
        self.assertEqual(image_post["image"]["items"][0]["url"], self.post_image_media.url)
        self.assertIsNone(image_post["video"])
        self.assertEqual(alias_posts[str(self.post.id)]["mediaUrl"], self.post_image_media.url)
        self.assertEqual(
            feed_data_posts[str(self.post.id)]["image"]["items"][0]["id"],
            str(self.post_image_media.id),
        )

        video_post = feed_posts[str(self.trending_post.id)]
        self.assertEqual(video_post["mediaUrl"], self.trending_video_media.url)
        self.assertEqual(video_post["mediaType"], "video")
        self.assertEqual(video_post["media"][0]["type"], "VIDEO")
        self.assertEqual(video_post["video"]["url"], self.trending_video_media.url)
        self.assertEqual(
            video_post["video"]["thumbnailUrl"],
            self.trending_video_media.thumbnail_url,
        )
        self.assertEqual(video_post["video"]["duration"], 24)
        self.assertIsNone(video_post["image"])

        self.assertEqual(detail_post["mediaUrl"], self.post_image_media.url)
        self.assertEqual(detail_post["image"]["items"][0]["url"], self.post_image_media.url)
        self.assertIsNone(detail_post["video"])

    def test_create_circle_post_accepts_text_only_without_media(self):
        data = _graphql(
            """
            mutation CreateCirclePost($circleId: String!) {
              createCirclePost(circleId: $circleId, text: "Text-only testimony") {
                success
                error { code message }
                post { id text media { id } mediaUrl mediaType image { items { id } } video { url } }
              }
            }
            """,
            {"circleId": str(self.circle.id)},
            access_token=self.author_access_token,
        )

        payload = data["createCirclePost"]
        self.assertTrue(payload["success"])
        self.assertIsNone(payload["error"])
        self.assertEqual(payload["post"]["text"], "Text-only testimony")
        self.assertEqual(payload["post"]["media"], [])
        self.assertIsNone(payload["post"]["mediaUrl"])
        self.assertIsNone(payload["post"]["mediaType"])
        self.assertIsNone(payload["post"]["image"])
        self.assertIsNone(payload["post"]["video"])

    def test_create_circle_post_accepts_ready_image_media_ids(self):
        create_media = _make_media_file(
            user=self.author,
            storage_path="circle-posts/new-image.jpg",
            width=1200,
            height=900,
        )
        data = _graphql(
            """
            mutation CreateCirclePost($circleId: String!, $mediaIds: [String!]) {
              createCirclePost(circleId: $circleId, text: "Shared testimony", mediaIds: $mediaIds) {
                success
                error { code message }
                post {
                  id
                  text
                  media { id url type width height duration }
                  mediaUrl
                  mediaType
                  image { items { id url type width height } }
                  video { url }
                }
              }
            }
            """,
            {"circleId": str(self.circle.id), "mediaIds": [str(create_media.id)]},
            access_token=self.author_access_token,
        )

        payload = data["createCirclePost"]
        self.assertTrue(payload["success"])
        self.assertIsNone(payload["error"])
        self.assertEqual(payload["post"]["mediaType"], "image")
        self.assertEqual(payload["post"]["mediaUrl"], create_media.url)
        self.assertEqual(payload["post"]["media"][0]["id"], str(create_media.id))
        self.assertEqual(payload["post"]["image"]["items"][0]["url"], create_media.url)
        self.assertIsNone(payload["post"]["video"])

        created_post = CirclePost.objects.get(id=payload["post"]["id"])
        self.assertEqual(created_post.image_url, "")
        self.assertEqual(created_post.media_url, "")
        self.assertEqual(
            list(created_post.media_files.values_list("id", flat=True)), [create_media.id]
        )

    def test_create_circle_post_accepts_ready_video_media_ids(self):
        create_media = _make_media_file(
            user=self.author,
            storage_path="circle-posts/new-video.mp4",
            media_type=StoredMediaType.VIDEO,
            thumbnail_path="circle-posts/new-video-thumb.jpg",
            width=1280,
            height=720,
            duration=31,
        )
        data = _graphql(
            """
            mutation CreateCirclePost($circleId: String!, $mediaIds: [String!], $mediaType: MediaType) {
              createCirclePost(
                circleId: $circleId
                mediaIds: $mediaIds
                mediaType: $mediaType
              ) {
                success
                error { code message }
                post {
                  id
                  media { id url thumbnailUrl type width height duration }
                  mediaUrl
                  mediaType
                  image { items { id } }
                  video { url thumbnailUrl duration width height }
                }
              }
            }
            """,
            {
                "circleId": str(self.circle.id),
                "mediaIds": [str(create_media.id)],
                "mediaType": "VIDEO",
            },
            access_token=self.author_access_token,
        )

        payload = data["createCirclePost"]
        self.assertTrue(payload["success"])
        self.assertEqual(payload["post"]["mediaType"], "video")
        self.assertEqual(payload["post"]["video"]["url"], create_media.url)
        self.assertEqual(payload["post"]["video"]["thumbnailUrl"], create_media.thumbnail_url)
        self.assertEqual(payload["post"]["video"]["duration"], 31)
        self.assertIsNone(payload["post"]["image"])

    @override_settings(MEDIA_URL_ALLOWLIST=["cdn.example.com"])
    def test_create_circle_post_accepts_media_urls_fallback(self):
        fallback_url = "https://cdn.example.com/circle-posts/fallback.jpg"
        response = type(
            "Response",
            (),
            {
                "headers": {"Content-Type": "image/jpeg"},
                "status_code": 200,
                "is_redirect": False,
                "is_permanent_redirect": False,
                "close": lambda self: None,
            },
        )()
        with patch("core.media.validators._head_external_media_url", return_value=response):
            data = _graphql(
                """
                mutation CreateCirclePost($circleId: String!, $mediaUrls: [String!], $width: Int, $height: Int) {
                  createCirclePost(
                    circleId: $circleId
                    text: "Fallback URL post"
                    mediaUrls: $mediaUrls
                    width: $width
                    height: $height
                  ) {
                    success
                    error { code message }
                    post {
                      id
                      media { id url type width height }
                      mediaUrl
                      mediaType
                      image { items { id url width height } }
                    }
                  }
                }
                """,
                {
                    "circleId": str(self.circle.id),
                    "mediaUrls": [fallback_url],
                    "width": 800,
                    "height": 600,
                },
                access_token=self.author_access_token,
            )

        payload = data["createCirclePost"]
        self.assertTrue(payload["success"])
        self.assertEqual(payload["post"]["mediaType"], "image")
        self.assertEqual(payload["post"]["image"]["items"][0]["width"], 800)
        created_post = CirclePost.objects.get(id=payload["post"]["id"])
        attached_media = created_post.media_files.get()
        self.assertEqual(attached_media.storage_path, fallback_url)
        self.assertEqual(attached_media.status, MediaStatus.READY)

    def test_create_circle_post_rejects_unready_media_ids(self):
        mutation = """
            mutation CreateCirclePost($circleId: String!, $mediaIds: [String!]) {
              createCirclePost(circleId: $circleId, mediaIds: $mediaIds) {
                success
                error { code message }
                post { id }
              }
            }
        """
        for status in (MediaStatus.PENDING, MediaStatus.PROCESSING, MediaStatus.FAILED):
            media_file = _make_media_file(
                user=self.author,
                storage_path=f"circle-posts/{status}.jpg",
                status=status,
            )
            with self.subTest(status=status):
                data = _graphql(
                    mutation,
                    {"circleId": str(self.circle.id), "mediaIds": [str(media_file.id)]},
                    access_token=self.author_access_token,
                )
                self.assertFalse(data["createCirclePost"]["success"])
                self.assertEqual(data["createCirclePost"]["error"]["code"], "VALIDATION_ERROR")

    def test_create_circle_post_rejects_mixed_image_and_video_media(self):
        image_media = _make_media_file(
            user=self.author,
            storage_path="circle-posts/mixed-image.jpg",
        )
        video_media = _make_media_file(
            user=self.author,
            storage_path="circle-posts/mixed-video.mp4",
            media_type=StoredMediaType.VIDEO,
        )
        data = _graphql(
            """
            mutation CreateCirclePost($circleId: String!, $mediaIds: [String!]) {
              createCirclePost(circleId: $circleId, mediaIds: $mediaIds) {
                success
                error { code message }
                post { id }
              }
            }
            """,
            {
                "circleId": str(self.circle.id),
                "mediaIds": [str(image_media.id), str(video_media.id)],
            },
            access_token=self.author_access_token,
        )

        payload = data["createCirclePost"]
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"]["code"], "VALIDATION_ERROR")

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
            access_token=self.viewer_access_token,
        )

        feed_data = data["circleFeedData"]
        self.assertEqual(feed_data["bannerImage"], "https://example.com/banner.jpg")
        self.assertEqual(feed_data["profileImage"], "https://example.com/profile.jpg")
        self.assertEqual(feed_data["coverImage"], "https://example.com/cover.jpg")
        self.assertEqual(feed_data["suggestionCardImage"], "https://example.com/cover.jpg")
        self.assertEqual(feed_data["memberCount"], 1247)
        self.assertTrue(feed_data["isJoined"])
        self.assertEqual(
            feed_data["memberAvatars"],
            [
                "https://example.com/avatar.jpg",
                "https://example.com/viewer.jpg",
            ],
        )
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
            access_token=self.viewer_access_token,
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
            access_token=self.viewer_access_token,
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

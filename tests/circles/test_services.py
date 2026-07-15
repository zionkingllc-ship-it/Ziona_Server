"""
Phase 1 Tests: Circle Core System - Services Layer
Tests for:
- Circle discovery (get_all_circles, get_my_circles, get_suggested_circles)
- Membership (join_circle, leave_circle)
- Circle creation (create_circle)
- Error handling for all edge cases
"""

import importlib
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.apps import apps as django_apps
from django.test import TestCase
from django.test.utils import override_settings
from django.utils import timezone

from core.circles.models import Circle, CircleMembership, CirclePost
from core.circles.services import (
    create_circle,
    create_circle_post,
    ensure_circle_post_liked,
    get_all_circles,
    get_circle_by_id,
    get_circle_feed,
    get_circle_post,
    get_my_circles,
    get_suggested_circles,
    join_circle,
    leave_circle,
    pray_for_anchor,
    pray_for_circle_post,
)
from core.media.models import MediaFile, MediaStatus
from core.media.models import MediaType as StoredMediaType
from core.shared.exceptions import ZionaError


def _make_user(email, username=None):
    """Helper to create a user matching the custom User model."""
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    user = user_model.objects.create_user(email=email, password="testpass123")
    if username:
        user.username = username
        user.save(update_fields=["username"])
    return user


def _make_media_file(
    *,
    user,
    storage_path,
    media_type=StoredMediaType.IMAGE,
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
        status=status,
    )


@pytest.mark.django_db
class TestCircleDiscovery(TestCase):
    """Tests for circle listing and discovery"""

    def setUp(self):
        self.user1 = _make_user("user1@test.com", "testuser1")
        self.user2 = _make_user("user2@test.com", "testuser2")
        self.circle1 = Circle.objects.create(
            name="Christianity and Life Struggles",
            description="A safe community for believers.",
            cover_image="https://example.com/cover.jpg",
            created_by=self.user1,
        )
        self.circle2 = Circle.objects.create(
            name="Prayer & Intercession",
            description="Pray together.",
            cover_image="https://example.com/cover2.jpg",
            created_by=self.user1,
        )

    def test_get_all_circles_returns_active(self):
        """Should return only active, non-deleted circles"""
        circles = get_all_circles()
        self.assertEqual(len(circles), 2)

    def test_get_all_circles_excludes_deleted(self):
        """Should exclude soft-deleted circles"""
        from django.utils import timezone

        self.circle1.deleted_at = timezone.now()
        self.circle1.save()
        circles = get_all_circles()
        self.assertEqual(len(circles), 1)
        self.assertEqual(circles[0].name, "Prayer & Intercession")

    def test_get_all_circles_excludes_inactive(self):
        """Should exclude inactive circles"""
        self.circle1.is_active = False
        self.circle1.save()
        circles = get_all_circles()
        self.assertEqual(len(circles), 1)

    def test_get_all_circles_with_viewer_subscription(self):
        """Should set _is_viewer_subscribed correctly"""
        CircleMembership.objects.create(circle=self.circle1, user=self.user1, role="member")
        circles = get_all_circles(viewer_id=str(self.user1.id))
        c1 = [c for c in circles if c.id == self.circle1.id][0]
        c2 = [c for c in circles if c.id == self.circle2.id][0]
        self.assertTrue(c1._is_viewer_subscribed)
        self.assertFalse(c2._is_viewer_subscribed)

    def test_get_my_circles_returns_joined_only(self):
        """Should return only circles user has joined"""
        CircleMembership.objects.create(circle=self.circle1, user=self.user1, role="member")
        circles = get_my_circles(str(self.user1.id))
        self.assertEqual(len(circles), 1)
        self.assertEqual(circles[0].name, "Christianity and Life Struggles")

    def test_get_suggested_circles_excludes_joined(self):
        """Should exclude circles user already joined"""
        CircleMembership.objects.create(circle=self.circle1, user=self.user1, role="member")
        circles = get_suggested_circles(str(self.user1.id))
        self.assertEqual(len(circles), 1)
        self.assertEqual(circles[0].name, "Prayer & Intercession")


@pytest.mark.django_db
class TestCircleMembership(TestCase):
    """Tests for join/leave circle operations"""

    def setUp(self):
        self.user = _make_user("member@test.com", "memberuser")
        self.admin_user = _make_user("admin@test.com", "adminuser")
        self.circle = Circle.objects.create(
            name="Test Circle",
            description="A test circle",
            cover_image="https://example.com/cover.jpg",
            created_by=self.admin_user,
        )

    def test_join_circle_success(self):
        """Should create membership and return it"""
        membership = join_circle(str(self.user.id), str(self.circle.id))
        self.assertEqual(membership.role, "member")
        self.assertEqual(membership.circle, self.circle)
        self.assertTrue(
            CircleMembership.objects.filter(circle=self.circle, user=self.user).exists()
        )

    def test_join_circle_already_member_raises(self):
        """Should raise ALREADY_MEMBER error"""
        CircleMembership.objects.create(circle=self.circle, user=self.user, role="member")
        with self.assertRaises(ZionaError) as ctx:
            join_circle(str(self.user.id), str(self.circle.id))
        self.assertEqual(ctx.exception.code, "ALREADY_MEMBER")

    def test_join_circle_not_found_raises(self):
        """Should raise CIRCLE_NOT_FOUND for invalid UUID"""
        import uuid

        with self.assertRaises(ZionaError) as ctx:
            join_circle(str(self.user.id), str(uuid.uuid4()))
        self.assertEqual(ctx.exception.code, "CIRCLE_NOT_FOUND")

    def test_leave_circle_success(self):
        """Should delete membership"""
        CircleMembership.objects.create(circle=self.circle, user=self.user, role="member")
        result = leave_circle(str(self.user.id), str(self.circle.id))
        self.assertTrue(result)
        self.assertFalse(
            CircleMembership.objects.filter(circle=self.circle, user=self.user).exists()
        )

    def test_leave_circle_not_member_raises(self):
        """Should raise NOT_MEMBER error"""
        with self.assertRaises(ZionaError) as ctx:
            leave_circle(str(self.user.id), str(self.circle.id))
        self.assertEqual(ctx.exception.code, "NOT_MEMBER")

    def test_leave_circle_last_admin_raises(self):
        """Should prevent last admin from leaving"""
        CircleMembership.objects.create(circle=self.circle, user=self.admin_user, role="admin")
        with self.assertRaises(ZionaError) as ctx:
            leave_circle(str(self.admin_user.id), str(self.circle.id))
        self.assertEqual(ctx.exception.code, "CANNOT_LEAVE_LAST_ADMIN")


@pytest.mark.django_db
class TestCirclePostEngagement(TestCase):
    """Tests for circle post engagement helpers."""

    def setUp(self):
        self.user = _make_user("post-liker@test.com", "postliker")
        self.author = _make_user("post-author@test.com", "postauthor")
        self.outsider = _make_user("post-outsider@test.com", "postoutsider")
        self.circle = Circle.objects.create(
            name="Circle Post Engagement",
            description="A circle for post engagement",
            cover_image="https://example.com/cover.jpg",
            created_by=self.author,
        )
        CircleMembership.objects.create(circle=self.circle, user=self.author, role="admin")
        CircleMembership.objects.create(circle=self.circle, user=self.user, role="member")
        self.post = CirclePost.objects.create(
            circle=self.circle,
            user=self.author,
            text="A circle post",
        )

    def test_ensure_circle_post_liked_is_idempotent(self):
        from core.circles.models import CirclePostEngagement

        first = ensure_circle_post_liked(str(self.user.id), str(self.post.id))
        second = ensure_circle_post_liked(str(self.user.id), str(self.post.id))

        self.assertTrue(first["liked"])
        self.assertTrue(second["liked"])
        self.assertEqual(first["likes_count"], 1)
        self.assertEqual(second["likes_count"], 1)
        self.assertEqual(
            CirclePostEngagement.objects.filter(
                post=self.post,
                user=self.user,
                engagement_type="like",
            ).count(),
            1,
        )

    def test_get_circle_post_allows_preview_for_non_members(self):
        post = get_circle_post(str(self.post.id), viewer_id=str(self.outsider.id))

        self.assertEqual(post.id, self.post.id)
        self.assertFalse(post.is_liked_by_viewer)
        self.assertFalse(post.is_prayed_by_viewer)

    def test_ensure_circle_post_liked_requires_membership(self):
        with self.assertRaises(ZionaError) as ctx:
            ensure_circle_post_liked(str(self.outsider.id), str(self.post.id))

        self.assertEqual(ctx.exception.code, "NOT_MEMBER")

    def test_pray_for_circle_post_requires_membership(self):
        with self.assertRaises(ZionaError) as ctx:
            pray_for_circle_post(str(self.outsider.id), str(self.post.id))

        self.assertEqual(ctx.exception.code, "NOT_MEMBER")


@pytest.mark.django_db
class TestCircleContentVisibility(TestCase):
    def setUp(self):
        self.author = _make_user("circle-author-visibility@test.com", "circleauthorvisibility")
        self.member = _make_user("circle-member-visibility@test.com", "circlemembervisibility")
        self.outsider = _make_user(
            "circle-outsider-visibility@test.com", "circleoutsidervisibility"
        )
        self.circle = Circle.objects.create(
            name="Member Circle",
            description="Members only",
            cover_image="https://example.com/cover.jpg",
            created_by=self.author,
        )
        CircleMembership.objects.create(circle=self.circle, user=self.author, role="admin")
        CircleMembership.objects.create(circle=self.circle, user=self.member, role="member")
        self.anchor = django_apps.get_model("circles", "Anchor").objects.create(
            circle=self.circle,
            created_by=self.author,
            anchor_type="text",
            title="Today's Anchor",
            content="Hello",
            published_at=timezone.now(),
            expires_at=timezone.now() + timedelta(days=1),
        )

    def test_get_circle_by_id_allows_preview_for_non_members(self):
        preview_circle = get_circle_by_id(str(self.circle.id), viewer_id=str(self.outsider.id))
        member_circle = get_circle_by_id(str(self.circle.id), viewer_id=str(self.member.id))

        assert preview_circle.id == self.circle.id
        assert preview_circle._is_viewer_subscribed is False
        assert member_circle.id == self.circle.id
        assert member_circle._is_viewer_subscribed is True

    def test_get_circle_feed_allows_preview_for_non_members(self):
        post = CirclePost.objects.create(circle=self.circle, user=self.author, text="Preview post")

        posts, has_next, total_count = get_circle_feed(
            str(self.circle.id), viewer_id=str(self.outsider.id)
        )

        assert has_next is False
        assert total_count == 1
        assert [item.id for item in posts] == [post.id]

    def test_pray_for_anchor_requires_membership(self):
        with self.assertRaises(ZionaError) as ctx:
            pray_for_anchor(str(self.outsider.id), str(self.anchor.id))

        self.assertEqual(ctx.exception.code, "NOT_CIRCLE_MEMBER")


@pytest.mark.django_db
class TestCirclePostMediaCreation(TestCase):
    def setUp(self):
        self.author = _make_user("circle-author@test.com", "circleauthor")
        self.circle = Circle.objects.create(
            name="Circle Media",
            description="Circle for media post tests",
            cover_image="https://example.com/cover.jpg",
            created_by=self.author,
        )
        CircleMembership.objects.create(circle=self.circle, user=self.author, role="admin")

    def test_create_circle_post_accepts_text_only(self):
        post = create_circle_post(
            user_id=str(self.author.id),
            circle_id=str(self.circle.id),
            text="Text-only circle post",
        )

        self.assertEqual(post.text, "Text-only circle post")
        self.assertEqual(post.media_files.count(), 0)

    def test_create_circle_post_accepts_media_only(self):
        media_file = _make_media_file(
            user=self.author,
            storage_path="circle-posts/media-only.jpg",
        )

        post = create_circle_post(
            user_id=str(self.author.id),
            circle_id=str(self.circle.id),
            media_ids=[str(media_file.id)],
        )

        self.assertEqual(post.text, "")
        self.assertEqual(list(post.media_files.values_list("id", flat=True)), [media_file.id])

    def test_create_circle_post_accepts_text_and_media(self):
        media_file = _make_media_file(
            user=self.author,
            storage_path="circle-posts/text-and-media.jpg",
        )

        post = create_circle_post(
            user_id=str(self.author.id),
            circle_id=str(self.circle.id),
            text="Text with image",
            media_ids=[str(media_file.id)],
        )

        self.assertEqual(post.text, "Text with image")
        self.assertEqual(list(post.media_files.values_list("id", flat=True)), [media_file.id])

    @override_settings(MEDIA_URL_ALLOWLIST=["cdn.example.com"])
    def test_create_circle_post_accepts_media_urls_fallback(self):
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
            post = create_circle_post(
                user_id=str(self.author.id),
                circle_id=str(self.circle.id),
                text="Fallback media",
                media_urls=["https://cdn.example.com/circle-posts/fallback.jpg"],
                width=1024,
                height=768,
            )

        attached_media = list(post.media_files.all())
        self.assertEqual(len(attached_media), 1)
        self.assertEqual(
            attached_media[0].storage_path, "https://cdn.example.com/circle-posts/fallback.jpg"
        )
        self.assertEqual(attached_media[0].status, MediaStatus.READY)
        self.assertEqual(attached_media[0].width, 1024)
        self.assertEqual(attached_media[0].height, 768)

    def test_create_circle_post_rejects_mixed_media_types(self):
        image_media = _make_media_file(user=self.author, storage_path="circle-posts/mixed.jpg")
        video_media = _make_media_file(
            user=self.author,
            storage_path="circle-posts/mixed.mp4",
            media_type=StoredMediaType.VIDEO,
        )

        with self.assertRaises(ZionaError) as ctx:
            create_circle_post(
                user_id=str(self.author.id),
                circle_id=str(self.circle.id),
                media_ids=[str(image_media.id), str(video_media.id)],
            )

        self.assertEqual(ctx.exception.code, "VALIDATION_ERROR")

    def test_create_circle_post_rejects_unready_media_ids(self):
        for status in (MediaStatus.PENDING, MediaStatus.PROCESSING, MediaStatus.FAILED):
            media_file = _make_media_file(
                user=self.author,
                storage_path=f"circle-posts/{status}.jpg",
                status=status,
            )
            with self.subTest(status=status):
                with self.assertRaises(ZionaError) as ctx:
                    create_circle_post(
                        user_id=str(self.author.id),
                        circle_id=str(self.circle.id),
                        media_ids=[str(media_file.id)],
                    )
                self.assertEqual(ctx.exception.code, "VALIDATION_ERROR")


@pytest.mark.django_db
class TestCirclePostMediaMigration(TestCase):
    def test_backfill_migrates_legacy_circle_post_urls_into_media_files(self):
        author = _make_user("legacy-circle@test.com", "legacycircle")
        circle = Circle.objects.create(
            name="Legacy Circle",
            description="Legacy media migration",
            cover_image="https://example.com/cover.jpg",
            created_by=author,
        )
        legacy_post = CirclePost.objects.create(
            circle=circle,
            user=author,
            text="Legacy media",
            image_url="https://cdn.example.com/circle-posts/legacy-image.jpg",
            media_url="https://cdn.example.com/circle-posts/legacy-video.mp4",
        )

        migration_module = importlib.import_module(
            "core.circles.migrations.0012_circlepost_media_files"
        )
        migration_module.backfill_circle_post_media_files(django_apps, None)

        legacy_post.refresh_from_db()
        attached_media = list(legacy_post.media_files.order_by("storage_path"))
        self.assertEqual(len(attached_media), 2)
        self.assertEqual(attached_media[0].status, MediaStatus.READY)
        self.assertEqual(attached_media[0].duration, None)
        self.assertEqual(attached_media[1].duration, None)
        self.assertEqual(
            {media.storage_path for media in attached_media},
            {
                "https://cdn.example.com/circle-posts/legacy-image.jpg",
                "https://cdn.example.com/circle-posts/legacy-video.mp4",
            },
        )
        self.assertEqual(
            {media.media_type for media in attached_media},
            {StoredMediaType.IMAGE, StoredMediaType.VIDEO},
        )


@pytest.mark.django_db
class TestCircleCreation(TestCase):
    """Tests for create_circle"""

    def setUp(self):
        self.admin = _make_user("creator@test.com", "creatoruser")

    def test_create_circle_success(self):
        """Should create circle with creator as admin"""
        circle = create_circle(
            str(self.admin.id), "New Circle", "A brand new circle", "https://example.com/cover.jpg"
        )
        self.assertEqual(circle.name, "New Circle")
        self.assertTrue(circle.is_active)

        # Creator should be admin
        membership = CircleMembership.objects.get(circle=circle, user=self.admin)
        self.assertEqual(membership.role, "admin")

    def test_create_circle_auto_admin_membership(self):
        """Creator automatically becomes first admin of the circle"""
        circle = create_circle(
            str(self.admin.id), "Admin Test", "desc", "https://example.com/c.jpg"
        )
        self.assertEqual(CircleMembership.objects.filter(circle=circle, role="admin").count(), 1)

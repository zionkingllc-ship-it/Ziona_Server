"""
Phase 2 Tests: Anchor System - Services Layer
Tests for:
- calculate_time_remaining formatting
- get_active_anchor (with cache)
- get_anchor_history
- create_anchor (validation, scheduling, overlap prevention)
"""
from datetime import timedelta

import pytest
from django.test import TestCase
from django.utils import timezone

from core.circles.anchor_services import (
    calculate_time_remaining,
    create_anchor,
    get_active_anchor,
    get_anchor_history,
    invalidate_active_anchor_cache,
)
from core.circles.models import Anchor, Circle, CircleMembership
from core.shared.exceptions import ZionaError


def _make_user(email, username=None):
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    user = user_model.objects.create_user(email=email, password="testpass123")
    if username:
        user.username = username
        user.save(update_fields=["username"])
    return user


def _make_circle_with_admin(admin_user, name="Test Circle"):
    circle = Circle.objects.create(
        name=name,
        description="A test circle",
        cover_image="https://example.com/cover.jpg",
        created_by=admin_user,
    )
    CircleMembership.objects.create(circle=circle, user=admin_user, role="admin")
    return circle


@pytest.mark.django_db
class TestTimeRemaining(TestCase):
    """Tests for calculate_time_remaining"""

    def test_time_remaining_future(self):
        """Should format as 'Xh Ym Zs'"""
        expires = timezone.now() + timedelta(hours=23, minutes=10, seconds=23)
        result = calculate_time_remaining(expires)
        self.assertTrue(result.startswith("23h 10m"))

    def test_time_remaining_expired(self):
        """Should return '0h 0m 0s' when expired"""
        expires = timezone.now() - timedelta(hours=1)
        result = calculate_time_remaining(expires)
        self.assertEqual(result, "0h 0m 0s")

    def test_time_remaining_just_now(self):
        """Should return very small time when about to expire"""
        expires = timezone.now() + timedelta(seconds=5)
        result = calculate_time_remaining(expires)
        self.assertTrue(result.startswith("0h 0m"))


@pytest.mark.django_db
class TestGetActiveAnchor(TestCase):
    """Tests for get_active_anchor with caching"""

    def setUp(self):
        self.admin = _make_user("admin@test.com", "admin1")
        self.circle = _make_circle_with_admin(self.admin)
        now = timezone.now()
        self.anchor = Anchor.objects.create(
            circle=self.circle,
            created_by=self.admin,
            anchor_type="bible_verse",
            title="John 3:16",
            content="For God so loved the world...",
            scripture_book="John",
            scripture_chapter=3,
            scripture_verse_start=16,
            scripture_text="For God so loved the world...",
            published_at=now - timedelta(hours=1),
            expires_at=now + timedelta(hours=23),
        )

    def test_returns_active_anchor(self):
        """Should return the currently active anchor"""
        invalidate_active_anchor_cache(str(self.circle.id))
        anchor = get_active_anchor(str(self.circle.id))
        self.assertIsNotNone(anchor)
        self.assertEqual(anchor.title, "John 3:16")

    def test_returns_none_when_expired(self):
        """Should return None when anchor expired"""
        self.anchor.expires_at = timezone.now() - timedelta(hours=1)
        self.anchor.save()
        invalidate_active_anchor_cache(str(self.circle.id))
        anchor = get_active_anchor(str(self.circle.id))
        self.assertIsNone(anchor)

    def test_returns_none_for_future_anchor(self):
        """Should not return anchors that aren't published yet"""
        self.anchor.published_at = timezone.now() + timedelta(hours=5)
        self.anchor.expires_at = timezone.now() + timedelta(hours=29)
        self.anchor.save()
        invalidate_active_anchor_cache(str(self.circle.id))
        anchor = get_active_anchor(str(self.circle.id))
        self.assertIsNone(anchor)


@pytest.mark.django_db
class TestAnchorHistory(TestCase):
    """Tests for get_anchor_history"""

    def setUp(self):
        self.admin = _make_user("hist@test.com", "histadmin")
        self.circle = _make_circle_with_admin(self.admin)
        now = timezone.now()
        for i in range(5):
            Anchor.objects.create(
                circle=self.circle,
                created_by=self.admin,
                anchor_type="bible_verse",
                title=f"Anchor {i}",
                scripture_book="Psalms",
                published_at=now - timedelta(days=i + 1),
                expires_at=now - timedelta(days=i),
            )

    def test_returns_all_anchors(self):
        """Should return all past anchors ordered by published_at DESC"""
        anchors = get_anchor_history(str(self.circle.id))
        self.assertEqual(len(anchors), 5)

    def test_respects_limit(self):
        """Should respect the limit parameter"""
        anchors = get_anchor_history(str(self.circle.id), limit=2)
        self.assertEqual(len(anchors), 2)


@pytest.mark.django_db
class TestCreateAnchor(TestCase):
    """Tests for create_anchor"""

    def setUp(self):
        self.admin = _make_user("creator@test.com", "creator1")
        self.member = _make_user("member@test.com", "member1")
        self.circle = _make_circle_with_admin(self.admin)
        CircleMembership.objects.create(circle=self.circle, user=self.member, role="member")

    def test_create_bible_verse_anchor(self):
        """Should create a bible verse anchor with scripture reference"""
        anchor = create_anchor(
            creator_id=str(self.admin.id),
            circle_id=str(self.circle.id),
            anchor_type="bible_verse",
            title="John 3:16",
            scripture_book="John",
            scripture_chapter=3,
            scripture_verse_start=16,
            scripture_text="For God so loved the world...",
        )
        self.assertEqual(anchor.anchor_type, "bible_verse")
        self.assertEqual(anchor.scripture_book, "John")
        # Expires 24h after published_at
        diff = anchor.expires_at - anchor.published_at
        self.assertAlmostEqual(diff.total_seconds(), 86400, delta=5)

    def test_create_devotional_with_pages(self):
        """Should create devotional anchor with multiple pages"""
        pages = [
            {"title": "Page 1", "content": "First devotional content"},
            {"title": "Page 2", "content": "Second devotional content"},
        ]
        anchor = create_anchor(
            creator_id=str(self.admin.id),
            circle_id=str(self.circle.id),
            anchor_type="devotional",
            title="Morning Devotion",
            content="Overview",
            pages=pages,
        )
        self.assertEqual(anchor.pages.count(), 2)
        self.assertEqual(anchor.pages.first().page_number, 1)

    def test_non_admin_cannot_create(self):
        """Should raise NOT_CIRCLE_ADMIN for non-admin members"""
        with self.assertRaises(ZionaError) as ctx:
            create_anchor(
                creator_id=str(self.member.id),
                circle_id=str(self.circle.id),
                anchor_type="bible_verse",
                title="Test",
                scripture_book="Genesis",
            )
        self.assertEqual(ctx.exception.code, "NOT_CIRCLE_ADMIN")

    def test_invalid_anchor_type_raises(self):
        """Should raise INVALID_ANCHOR_TYPE"""
        with self.assertRaises(ZionaError) as ctx:
            create_anchor(
                creator_id=str(self.admin.id),
                circle_id=str(self.circle.id),
                anchor_type="podcast",
                title="Test",
            )
        self.assertEqual(ctx.exception.code, "INVALID_ANCHOR_TYPE")

    def test_bible_verse_missing_scripture_raises(self):
        """Should raise MISSING_SCRIPTURE_REFERENCE"""
        with self.assertRaises(ZionaError) as ctx:
            create_anchor(
                creator_id=str(self.admin.id),
                circle_id=str(self.circle.id),
                anchor_type="bible_verse",
                title="Test",
                # Missing scripture_book
            )
        self.assertEqual(ctx.exception.code, "MISSING_SCRIPTURE_REFERENCE")

    def test_schedule_in_past_raises(self):
        """Should raise CANNOT_SCHEDULE_PAST"""
        with self.assertRaises(ZionaError) as ctx:
            create_anchor(
                creator_id=str(self.admin.id),
                circle_id=str(self.circle.id),
                anchor_type="image",
                title="Old Image",
                media_url="https://example.com/img.jpg",
                published_at=timezone.now() - timedelta(hours=1),
            )
        self.assertEqual(ctx.exception.code, "CANNOT_SCHEDULE_PAST")

    def test_schedule_too_far_raises(self):
        """Should raise SCHEDULE_TOO_FAR"""
        with self.assertRaises(ZionaError) as ctx:
            create_anchor(
                creator_id=str(self.admin.id),
                circle_id=str(self.circle.id),
                anchor_type="image",
                title="Future Image",
                media_url="https://example.com/img.jpg",
                published_at=timezone.now() + timedelta(days=31),
            )
        self.assertEqual(ctx.exception.code, "SCHEDULE_TOO_FAR")

    def test_overlapping_anchor_raises(self):
        """Should raise OVERLAPPING_ANCHOR when another anchor already occupies the time window"""
        # Create first anchor
        create_anchor(
            creator_id=str(self.admin.id),
            circle_id=str(self.circle.id),
            anchor_type="image",
            title="First",
            media_url="https://example.com/img1.jpg",
        )
        # Try to create overlapping anchor
        with self.assertRaises(ZionaError) as ctx:
            create_anchor(
                creator_id=str(self.admin.id),
                circle_id=str(self.circle.id),
                anchor_type="image",
                title="Second",
                media_url="https://example.com/img2.jpg",
            )
        self.assertEqual(ctx.exception.code, "OVERLAPPING_ANCHOR")

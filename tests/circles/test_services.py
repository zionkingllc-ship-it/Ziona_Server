"""
Phase 1 Tests: Circle Core System - Services Layer
Tests for:
- Circle discovery (get_all_circles, get_my_circles, get_suggested_circles)
- Membership (join_circle, leave_circle)
- Circle creation (create_circle)
- Error handling for all edge cases
"""
import pytest
from django.test import TestCase

from core.circles.models import Circle, CircleMembership
from core.circles.services import (
    create_circle,
    ensure_circle_post_liked,
    get_all_circles,
    get_my_circles,
    get_suggested_circles,
    join_circle,
    leave_circle,
)
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
        from core.circles.models import CirclePost

        self.user = _make_user("post-liker@test.com", "postliker")
        self.author = _make_user("post-author@test.com", "postauthor")
        self.circle = Circle.objects.create(
            name="Circle Post Engagement",
            description="A circle for post engagement",
            cover_image="https://example.com/cover.jpg",
            created_by=self.author,
        )
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

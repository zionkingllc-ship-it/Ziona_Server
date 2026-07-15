"""Circle discovery + membership (list, join, leave, create).

Split from the former core/circles/services.py (no behavior change).
"""

import logging

from django.db import transaction
from django.db.models import Count, Prefetch

from core.circles.access import has_circle_membership
from core.circles.models import (
    Circle,
    CircleMembership,
)
from core.engagement.hidden_content import exclude_hidden_circle_content
from core.shared.exceptions import ZionaError

logger = logging.getLogger("core.circles")

# Shorthand error codes
CIRCLE_NOT_FOUND = "CIRCLE_NOT_FOUND"
CIRCLE_INACTIVE = "CIRCLE_INACTIVE"
ALREADY_MEMBER = "ALREADY_MEMBER"
NOT_MEMBER = "NOT_MEMBER"


def _exclude_hidden_circles(queryset, viewer_id: str | None):
    return exclude_hidden_circle_content(queryset, viewer_id, target_type="circle")


def get_circle_by_id(circle_id: str, viewer_id: str | None = None) -> Circle | None:
    """Fetch a single active, visible circle.

    Circle detail/feed preview is intentionally readable by non-members so users
    can decide whether to join. Mutations and engagement paths still call
    ``require_circle_membership`` and remain member-only.
    """
    queryset = Circle.objects.filter(id=circle_id, is_active=True, deleted_at__isnull=True)
    queryset = _exclude_hidden_circles(queryset, viewer_id)
    circle = queryset.first()
    if not circle:
        return None
    circle._is_viewer_subscribed = has_circle_membership(viewer_id, str(circle.id))
    return circle


def get_all_circles(
    viewer_id: str | None = None, limit: int = 20, cursor: str | None = None
) -> list[Circle]:
    """
    Get all active Circles with pagination
    OPTIMIZED: Prevent N+1 queries using select_related, annotate, and prefetch_related
    """
    # 1. Base Query: Active circles, not deleted
    queryset = (
        Circle.objects.filter(is_active=True, deleted_at__isnull=True)
        .select_related("created_by")
        .annotate(member_count=Count("memberships"))
        .prefetch_related(
            Prefetch(
                "memberships",
                queryset=CircleMembership.objects.select_related("user").order_by(
                    "role", "joined_at"
                )[:4],
                to_attr="preview_memberships",
            )
        )
        .order_by("-created_at")
    )
    queryset = _exclude_hidden_circles(queryset, viewer_id)

    # 2. Pagination (cursor assumes created_at string for now)
    if cursor:
        queryset = queryset.filter(created_at__lt=cursor)

    circles = list(queryset[:limit])

    # 3. Dynamic Subscription check
    if viewer_id:
        # Get all circle IDs the user is subscribed to in one query
        subscribed_ids = set(
            CircleMembership.objects.filter(
                user_id=viewer_id, circle_id__in=[c.id for c in circles]
            ).values_list("circle_id", flat=True)
        )

        for circle in circles:
            # We monkey-patch a temporary attribute to prevent N+1 in the resolver
            circle._is_viewer_subscribed = circle.id in subscribed_ids
    else:
        for circle in circles:
            circle._is_viewer_subscribed = False

    return circles


def get_my_circles(user_id: str, limit: int = 20, cursor: str | None = None) -> list[Circle]:
    """Get Circles current user has joined"""
    queryset = (
        Circle.objects.filter(memberships__user_id=user_id, is_active=True, deleted_at__isnull=True)
        .select_related("created_by")
        .annotate(member_count=Count("memberships"))
        .order_by("-memberships__joined_at")
    )
    queryset = _exclude_hidden_circles(queryset, user_id)

    if cursor:
        # Assuming cursor is joined_at for my_circles
        queryset = queryset.filter(memberships__joined_at__lt=cursor)

    circles = list(queryset[:limit])
    for circle in circles:
        circle._is_viewer_subscribed = True

    return circles


def get_suggested_circles(user_id: str | None = None, limit: int = 10) -> list[Circle]:
    """Get recommended Circles (simple popular algorithm)"""
    queryset = Circle.objects.filter(is_active=True, deleted_at__isnull=True)

    if user_id:
        queryset = queryset.exclude(memberships__user_id=user_id)
    queryset = _exclude_hidden_circles(queryset, user_id)

    circles = list(
        queryset.annotate(member_count=Count("memberships")).order_by("-member_count")[:limit]
    )

    for circle in circles:
        circle._is_viewer_subscribed = False

    return circles


@transaction.atomic
def join_circle(user_id: str, circle_id: str) -> CircleMembership:
    """Subscribe user to Circle"""
    try:
        circle = Circle.objects.get(id=circle_id, is_active=True, deleted_at__isnull=True)
    except Circle.DoesNotExist:
        raise ZionaError(
            message="Circle does not exist or has been deleted", code=CIRCLE_NOT_FOUND
        ) from None

    # Check if already a member
    if CircleMembership.objects.filter(circle=circle, user_id=user_id).exists():
        raise ZionaError(message="You are already a member of this Circle", code=ALREADY_MEMBER)

    return CircleMembership.objects.create(circle=circle, user_id=user_id, role="member")


@transaction.atomic
def leave_circle(user_id: str, circle_id: str) -> bool:
    """Unsubscribe user from Circle"""
    try:
        membership = CircleMembership.objects.get(circle_id=circle_id, user_id=user_id)
    except CircleMembership.DoesNotExist:
        raise ZionaError(message="You are not a member of this Circle", code=NOT_MEMBER) from None

    # Prevent last admin from leaving (Basic check)
    if membership.role == "admin":
        admin_count = CircleMembership.objects.filter(circle_id=circle_id, role="admin").count()
        if admin_count <= 1:
            raise ZionaError(
                message="Cannot leave Circle as the only admin", code="CANNOT_LEAVE_LAST_ADMIN"
            )

    membership.delete()
    return True


@transaction.atomic
def create_circle(creator_id: str, name: str, description: str, cover_image: str) -> Circle:
    """Create new Circle (admin only - permissions checked at schema layer)"""
    circle = Circle.objects.create(
        name=name,
        description=description,
        cover_image=cover_image,
        created_by_id=creator_id,
        is_active=True,
    )

    # Creator automatically becomes first admin
    CircleMembership.objects.create(circle=circle, user_id=creator_id, role="admin")

    return circle

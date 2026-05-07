from django.db import transaction
from django.db.models import Count, F, Prefetch

from core.circles.models import (
    Anchor,
    AnchorEngagement,
    Circle,
    CircleMembership,
    CirclePost,
    CirclePostEngagement,
)
from core.shared.exceptions import ZionaError

# Shorthand error codes
CIRCLE_NOT_FOUND = "CIRCLE_NOT_FOUND"
CIRCLE_INACTIVE = "CIRCLE_INACTIVE"
ALREADY_MEMBER = "ALREADY_MEMBER"
NOT_MEMBER = "NOT_MEMBER"


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


# ──────────────────────────────────────────────
#  PHASE 5: Circle Feed & Engagement Services
# ──────────────────────────────────────────────

CIRCLE_POST_NOT_FOUND = "CIRCLE_POST_NOT_FOUND"
ANCHOR_NOT_FOUND = "ANCHOR_NOT_FOUND"
VALIDATION_ERROR = "VALIDATION_ERROR"


def get_circle_feed(
    circle_id: str, page: int = 1, page_size: int = 20
) -> tuple[list[CirclePost], bool, int]:
    """
    Return paginated CirclePosts for a given circle.

    Returns:
        (posts, has_next_page, total_count)
    """
    try:
        circle = Circle.objects.get(id=circle_id, is_active=True, deleted_at__isnull=True)
    except Circle.DoesNotExist:
        raise ZionaError(
            message="Circle does not exist or has been deleted", code=CIRCLE_NOT_FOUND
        ) from None

    queryset = (
        CirclePost.objects.filter(circle=circle, deleted_at__isnull=True)
        .select_related("user")
        .order_by("-created_at")
    )

    total_count = queryset.count()
    offset = (page - 1) * page_size
    posts = list(queryset[offset : offset + page_size + 1])

    has_next_page = len(posts) > page_size
    return posts[:page_size], has_next_page, total_count


@transaction.atomic
def create_circle_post(
    user_id: str,
    circle_id: str,
    text: str = "",
    image_url: str = "",
    media_url: str = "",
) -> CirclePost:
    """
    Create a post inside a Circle.

    Raises:
        ZionaError(CIRCLE_NOT_FOUND) if the circle does not exist.
        ZionaError(NOT_MEMBER) if the user is not a circle member.
        ZionaError(VALIDATION_ERROR) if no content is provided.
    """
    try:
        circle = Circle.objects.get(id=circle_id, is_active=True, deleted_at__isnull=True)
    except Circle.DoesNotExist:
        raise ZionaError(
            message="Circle does not exist or has been deleted", code=CIRCLE_NOT_FOUND
        ) from None

    if not CircleMembership.objects.filter(circle=circle, user_id=user_id).exists():
        raise ZionaError(
            message="You must be a member of this Circle to post",
            code=NOT_MEMBER,
        )

    if not any([text.strip(), image_url.strip(), media_url.strip()]):
        raise ZionaError(
            message="A post must have at least one of: text, image, or media",
            code=VALIDATION_ERROR,
        )

    return CirclePost.objects.create(
        circle=circle,
        user_id=user_id,
        text=text.strip(),
        image_url=image_url.strip(),
        media_url=media_url.strip(),
    )


@transaction.atomic
def pray_for_anchor(user_id: str, anchor_id: str) -> dict:
    """
    Toggle a pray engagement on an Anchor.
    Creates the engagement if it does not exist, deletes it if it does.
    Updates Anchor.prayed_count atomically using F() expressions.

    Returns:
        {"prayed": bool, "prayed_count": int}
    """
    try:
        anchor = Anchor.objects.select_for_update().get(id=anchor_id, deleted_at__isnull=True)
    except Anchor.DoesNotExist:
        raise ZionaError(message="Anchor not found", code=ANCHOR_NOT_FOUND) from None

    engagement, created = AnchorEngagement.objects.get_or_create(
        anchor=anchor, user_id=user_id, engagement_type="pray"
    )

    if created:
        Anchor.objects.filter(id=anchor_id).update(prayed_count=F("prayed_count") + 1)
        anchor.refresh_from_db(fields=["prayed_count"])
        return {"prayed": True, "prayed_count": anchor.prayed_count}
    engagement.delete()
    Anchor.objects.filter(id=anchor_id).update(prayed_count=F("prayed_count") - 1)
    anchor.refresh_from_db(fields=["prayed_count"])
    return {"prayed": False, "prayed_count": max(anchor.prayed_count, 0)}


@transaction.atomic
def like_anchor(user_id: str, anchor_id: str) -> dict:
    """
    Toggle a like engagement on an Anchor.
    Updates Anchor.anchor_liked_count atomically using F() expressions.

    Returns:
        {"liked": bool, "anchor_liked_count": int}
    """
    try:
        anchor = Anchor.objects.select_for_update().get(id=anchor_id, deleted_at__isnull=True)
    except Anchor.DoesNotExist:
        raise ZionaError(message="Anchor not found", code=ANCHOR_NOT_FOUND) from None

    engagement, created = AnchorEngagement.objects.get_or_create(
        anchor=anchor, user_id=user_id, engagement_type="like"
    )

    if created:
        Anchor.objects.filter(id=anchor_id).update(anchor_liked_count=F("anchor_liked_count") + 1)
        anchor.refresh_from_db(fields=["anchor_liked_count"])
        return {"liked": True, "anchor_liked_count": anchor.anchor_liked_count}
    engagement.delete()
    Anchor.objects.filter(id=anchor_id).update(anchor_liked_count=F("anchor_liked_count") - 1)
    anchor.refresh_from_db(fields=["anchor_liked_count"])
    return {"liked": False, "anchor_liked_count": max(anchor.anchor_liked_count, 0)}


@transaction.atomic
def pray_for_circle_post(user_id: str, post_id: str) -> dict:
    """
    Toggle a pray engagement on a CirclePost.
    Updates CirclePost.prayed_count atomically using F() expressions.

    Returns:
        {"prayed": bool, "prayed_count": int}
    """
    try:
        post = CirclePost.objects.select_for_update().get(id=post_id, deleted_at__isnull=True)
    except CirclePost.DoesNotExist:
        raise ZionaError(message="Post not found", code=CIRCLE_POST_NOT_FOUND) from None

    engagement, created = CirclePostEngagement.objects.get_or_create(post=post, user_id=user_id)

    if created:
        CirclePost.objects.filter(id=post_id).update(prayed_count=F("prayed_count") + 1)
        post.refresh_from_db(fields=["prayed_count"])
        return {"prayed": True, "prayed_count": post.prayed_count}
    engagement.delete()
    CirclePost.objects.filter(id=post_id).update(prayed_count=F("prayed_count") - 1)
    post.refresh_from_db(fields=["prayed_count"])
    return {"prayed": False, "prayed_count": max(post.prayed_count, 0)}

from core.circles.models import Circle, CircleMembership


def can_view_circle(user_id: str, circle_id: str) -> bool:
    """All active Circles are public"""
    return Circle.objects.filter(id=circle_id, is_active=True, deleted_at__isnull=True).exists()


def can_join_circle(user_id: str, circle_id: str) -> bool:
    """User must be authenticated, Circle must be active"""
    if not user_id:
        return False
    return can_view_circle(user_id, circle_id)


def can_create_circle(user: object) -> bool:
    """User must be admin"""
    if not user or not user.is_authenticated:
        return False
    # Simplified check for phase 1 - relying on is_staff or equivalent
    return getattr(user, "is_staff", False)


def can_create_anchor(user_id: str, circle_id: str) -> bool:
    """User must be Circle admin or platform admin"""
    if not user_id:
        return False

    try:
        membership = CircleMembership.objects.get(circle_id=circle_id, user_id=user_id)
        return membership.is_admin()
    except CircleMembership.DoesNotExist:
        # Check if platform admin (using User model directly logic could go here)
        return False

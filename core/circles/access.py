"""Shared membership guards for circle content access."""

from __future__ import annotations

from core.circles.models import CircleMembership
from core.shared.exceptions import ZionaError


def has_circle_membership(user_id: str | None, circle_id: str) -> bool:
    """Return whether the user is an active member of the given circle."""
    if not user_id:
        return False
    return CircleMembership.objects.filter(circle_id=circle_id, user_id=user_id).exists()


def require_circle_membership(
    user_id: str | None,
    circle_id: str,
    *,
    code: str = "NOT_CIRCLE_MEMBER",
    message: str = "You must join the Circle to access this content",
) -> None:
    """Raise when the viewer is not a member of the circle."""
    if not has_circle_membership(user_id, circle_id):
        raise ZionaError(message=message, code=code)

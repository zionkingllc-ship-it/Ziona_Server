"""Shared rules for claiming, changing, and temporarily reserving @handles."""

from datetime import timedelta

from django.utils import timezone

from core.users.models import User, UsernameReservation

GRACE_PERIOD = timedelta(hours=48)
MAX_GRACE_CHANGES = 3
CHANGE_COOLDOWN = timedelta(days=30)
RESERVATION_PERIOD = timedelta(days=14)


def normalized_username(username: str) -> str:
    """Handles are ASCII; lowercasing defines their case-insensitive identity."""
    return username.lower()


def username_is_available(username: str, *, owner_id=None, now=None) -> bool:
    """Check live and soft-deleted users, then unexpired released-name claims."""
    now = now or timezone.now()
    users = User.all_objects.filter(username__iexact=username)
    if owner_id is not None:
        users = users.exclude(id=owner_id)
    if users.exists():
        return False
    reservation = UsernameReservation.objects.filter(
        username=normalized_username(username), expires_at__gt=now
    ).first()
    return reservation is None or (
        owner_id is not None and str(reservation.owner_id) == str(owner_id)
    )


def grace_changes_remaining(user: User, *, now=None) -> int:
    now = now or timezone.now()
    if now >= user.created_at + GRACE_PERIOD:
        return 0
    return max(0, MAX_GRACE_CHANGES - user.grace_username_changes)


def username_editable_until(user: User, *, now=None):
    """Return the grace deadline while free edits remain, otherwise None."""
    return user.created_at + GRACE_PERIOD if grace_changes_remaining(user, now=now) else None


def reserve_released_username(username: str | None, owner_id, *, now=None) -> None:
    """Keep a released handle out of circulation for 14 days, including case variants."""
    if not username:
        return
    now = now or timezone.now()
    UsernameReservation.objects.update_or_create(
        username=normalized_username(username),
        defaults={"owner_id": owner_id, "expires_at": now + RESERVATION_PERIOD},
    )

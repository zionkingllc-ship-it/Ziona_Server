"""Authentication activity helpers.

Centralizes updates to fields used by admin analytics so every token-issuing
auth path records activity consistently.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

logger = logging.getLogger(__name__)

# user_id -> the UTC day this process last recorded them. Lets every request
# after a user's first one of the day skip the database entirely. Per-process
# on purpose: Redis would spend Upstash budget on every authenticated request,
# and a duplicate insert from another process is harmless (ignore_conflicts).
_recorded_today: dict[str, date] = {}
_RECORDED_TODAY_MAX = 50_000


def record_successful_auth(user, ip_address: str | None = None) -> None:
    """Record a successful authentication or authenticated session refresh."""
    user.last_login = timezone.now()
    update_fields = ["last_login", "updated_at"]

    if ip_address is not None:
        user.last_login_ip = ip_address
        update_fields.append("last_login_ip")

    user.save(update_fields=update_fields)
    record_daily_activity(user.id)


def record_daily_activity(user_id) -> None:
    """Mark ``user_id`` active today. At most one INSERT per user per day per process.

    Never raises: losing one activity mark must not fail the request it rides on.
    """
    from core.users.models import UserDailyActivity

    today = timezone.now().date()
    key = str(user_id)
    if _recorded_today.get(key) == today:
        return

    try:
        # Savepoint, so a failure cannot poison a surrounding transaction.
        with transaction.atomic():
            UserDailyActivity.objects.bulk_create(
                [UserDailyActivity(user_id=user_id, date=today)],
                ignore_conflicts=True,
            )
    except Exception:
        logger.warning("daily_activity_record_failed", extra={"user_id": key}, exc_info=True)
        return

    if len(_recorded_today) >= _RECORDED_TODAY_MAX:
        _recorded_today.clear()
    _recorded_today[key] = today


def active_users_between(start: datetime, end: datetime | None = None):
    """Non-deleted users active in [start, end); ``end=None`` means up to now.

    Active = a daily-activity row in the window OR ``last_login`` in it. The
    ``last_login`` arm keeps days from before activity rows existed counted
    exactly as they always were; the activity arm adds the users ``last_login``
    misses. ``start``/``end`` must fall on UTC midnight, as activity is per day.
    """
    from core.users.models import User, UserDailyActivity

    activity = UserDailyActivity.objects.filter(user_id=OuterRef("pk"), date__gte=start.date())
    logged_in = Q(last_login__gte=start)
    if end is not None:
        activity = activity.filter(date__lt=end.date())
        logged_in &= Q(last_login__lt=end)

    return User.objects.filter(deleted_at__isnull=True).filter(logged_in | Exists(activity))

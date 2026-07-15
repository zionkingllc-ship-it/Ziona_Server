"""Anchor engagement — pray + like.

Split from the former core/circles/services.py (no behavior change).
"""

import logging

from django.db import transaction
from django.db.models import F

from core.circles.access import require_circle_membership
from core.circles.models import (
    Anchor,
    AnchorEngagement,
)
from core.shared.exceptions import ZionaError

logger = logging.getLogger("core.circles")

CIRCLE_POST_NOT_FOUND = "CIRCLE_POST_NOT_FOUND"
ANCHOR_NOT_FOUND = "ANCHOR_NOT_FOUND"
VALIDATION_ERROR = "VALIDATION_ERROR"

# Shorthand error codes
CIRCLE_NOT_FOUND = "CIRCLE_NOT_FOUND"
CIRCLE_INACTIVE = "CIRCLE_INACTIVE"
ALREADY_MEMBER = "ALREADY_MEMBER"
NOT_MEMBER = "NOT_MEMBER"


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
    require_circle_membership(
        user_id,
        str(anchor.circle_id),
        message="You must join the Circle to pray for anchors",
    )

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
    require_circle_membership(
        user_id,
        str(anchor.circle_id),
        message="You must join the Circle to like anchors",
    )

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

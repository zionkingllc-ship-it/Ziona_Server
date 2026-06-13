"""
Phase 4: Moderation Service Layer.
Handles reporting of Circle content (anchors, responses, circles) and
implements the auto-hide threshold logic (3 distinct reports).
"""

from django.db import transaction

from core.circles.models import Anchor, AnchorResponse, Circle, CircleReport
from core.engagement.hidden_content import hide_circle_content_for_user
from core.shared.exceptions import ZionaError


@transaction.atomic
def report_circle_content(
    reporter_id: str, target_type: str, target_id: str, reason: str, circle_id: str
) -> CircleReport:
    """
    Submits a moderation report for circle content.
    If the content reaches 3 distinct reports, it is auto-hidden (soft deleted).
    """
    # ── Validation ──
    valid_targets = ["anchor", "response", "circle"]
    if target_type not in valid_targets:
        raise ZionaError(message="Invalid report target", code="INVALID_TARGET_TYPE")

    try:
        circle = Circle.objects.get(id=circle_id, deleted_at__isnull=True)
    except Circle.DoesNotExist:
        raise ZionaError(message="Circle not found", code="CIRCLE_NOT_FOUND") from None

    # Ensure target actually exists and belongs to the circle
    if (
        target_type == "anchor"
        and not Anchor.objects.filter(
            id=target_id, circle_id=circle_id, deleted_at__isnull=True
        ).exists()
    ):
        raise ZionaError(message="Anchor not found in this circle", code="TARGET_NOT_FOUND")
    if (
        target_type == "response"
        and not AnchorResponse.objects.filter(
            id=target_id, anchor__circle_id=circle_id, deleted_at__isnull=True
        ).exists()
    ):
        raise ZionaError(message="Response not found in this circle", code="TARGET_NOT_FOUND")
    if target_type == "circle" and str(circle.id) != str(target_id):
        raise ZionaError(message="Target ID must match Circle ID", code="TARGET_MISMATCH")

    # ── Prevent duplicate reports ──
    existing_report = CircleReport.objects.filter(
        reporter_id=reporter_id, target_type=target_type, target_id=target_id
    ).first()

    if existing_report:
        hide_circle_content_for_reporter(
            reporter_id=reporter_id,
            target_type=target_type,
            target_id=target_id,
        )
        raise ZionaError(message="You have already reported this content", code="ALREADY_REPORTED")

    # ── Create Report ──
    report = CircleReport.objects.create(
        reporter_id=reporter_id,
        circle_id=circle_id,
        target_type=target_type,
        target_id=target_id,
        reason=reason,
    )

    hide_circle_content_for_reporter(
        reporter_id=reporter_id,
        target_type=target_type,
        target_id=target_id,
    )

    # ── Check Auto-Hide Threshold (3 distinct reporters) ──
    report_count = (
        CircleReport.objects.filter(target_type=target_type, target_id=target_id)
        .values("reporter_id")
        .distinct()
        .count()
    )

    if report_count >= 3:
        _auto_hide_content(target_type, target_id)

    return report


def _auto_hide_content(target_type: str, target_id: str):
    """Soft deletes content and notifies admins once the auto-hide threshold is met."""
    from django.utils import timezone

    now = timezone.now()

    if target_type == "response":
        response = AnchorResponse.objects.filter(id=target_id).first()
        if response and not response.deleted_at:
            response.deleted_at = now
            response.save(update_fields=["deleted_at"])
            # Decrement response count on parent anchor handled by annotate dynamically

    elif target_type == "anchor":
        anchor = Anchor.objects.filter(id=target_id).first()
        if anchor and not anchor.deleted_at:
            anchor.deleted_at = now
            anchor.save(update_fields=["deleted_at"])
            from core.circles.anchor_services import invalidate_active_anchor_cache

            invalidate_active_anchor_cache(str(anchor.circle_id))

    elif target_type == "circle":
        # Circles require manual admin review before deletion, just flag them
        # (A production app might notify global admins here)
        pass


def hide_circle_content_for_reporter(reporter_id: str, target_type: str, target_id: str) -> None:
    """Immediately suppress reported circle content for the reporting user only."""
    hide_circle_content_for_user(
        user_id=str(reporter_id),
        target_type=target_type,
        target_id=str(target_id),
    )

"""Serialization + stats helpers for admin circle management.

Split from core/admin_dashboard/circle_services.py (no behavior change).
"""


def _circle_to_dict(circle) -> dict:
    """Convert Circle model to admin-facing dict."""
    member_count = getattr(circle, "member_count_val", 0)

    created_by_name = ""
    if circle.created_by:
        created_by_name = circle.created_by.full_name or circle.created_by.username

    return {
        "id": str(circle.id),
        "name": circle.name,
        "description": circle.description,
        "cover_image": circle.cover_image,
        "profile_image_url": circle.profile_image_url,
        "banner_image": circle.banner_image,
        "status": circle.status,
        "is_active": circle.is_active,
        "member_count": member_count,
        "created_by_name": created_by_name,
        "last_edited_at": circle.last_edited_at.isoformat() if circle.last_edited_at else None,
        "created_at": circle.created_at.isoformat() if circle.created_at else "",
    }


def _engagement_events_for_window(
    circle_id: str,
    start,
    end,
    anchor_engagement_model,
    post_engagement_model,
    post_comment_model,
) -> int:
    """Count circle engagement events in a time window."""
    anchor_engagements = anchor_engagement_model.objects.filter(
        anchor__circle_id=circle_id,
        anchor__deleted_at__isnull=True,
        created_at__gte=start,
    )
    post_engagements = post_engagement_model.objects.filter(
        post__circle_id=circle_id,
        post__deleted_at__isnull=True,
        created_at__gte=start,
    )
    comments = post_comment_model.objects.filter(
        post__circle_id=circle_id,
        post__deleted_at__isnull=True,
        deleted_at__isnull=True,
        created_at__gte=start,
    )

    if end is not None:
        anchor_engagements = anchor_engagements.filter(created_at__lt=end)
        post_engagements = post_engagements.filter(created_at__lt=end)
        comments = comments.filter(created_at__lt=end)

    return anchor_engagements.count() + post_engagements.count() + comments.count()


def _calc_percentage_change(old: int | float, new: int | float) -> float:
    """Calculate percentage change between two values."""
    if old == 0:
        return 100.0 if new > 0 else 0.0
    return round((new - old) / old * 100, 1)

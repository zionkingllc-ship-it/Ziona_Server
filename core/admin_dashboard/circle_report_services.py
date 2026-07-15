"""Admin listing of circle-content reports (circles.CircleReport).

Circle reports are separate from the global Report table; until now they
were only acted on by the 3-report auto-hide and invisible to admins.
"""

from django.db.models import Count, Q

# ──────────────────────────────────────────────
#  Circle reports (CircleReport — anchors, responses, circles)
# ──────────────────────────────────────────────

CIRCLE_REPORT_PAGE_MAX = 50


def list_circle_reports(
    status_filter: str = "",
    target_type_filter: str = "",
    circle_id: str = "",
    search: str = "",
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """List reports on circle content for the admin moderation queue.

    Circle reports live in circles.CircleReport (separate from the global
    Report table) and are otherwise only acted on by the 3-report auto-hide.
    This surfaces them to admins with a resolved content preview per target.
    """
    from core.circles.models import CircleReport

    page = max(1, page)
    page_size = min(max(1, page_size), CIRCLE_REPORT_PAGE_MAX)
    offset = (page - 1) * page_size

    qs = CircleReport.objects.select_related("reporter", "circle", "resolved_by").order_by(
        "-created_at"
    )

    if status_filter:
        qs = qs.filter(status=status_filter.strip().lower())
    if target_type_filter:
        qs = qs.filter(target_type=target_type_filter.strip().lower())
    if circle_id:
        qs = qs.filter(circle_id=circle_id)
    if search:
        qs = qs.filter(
            Q(reason__icontains=search)
            | Q(circle__name__icontains=search)
            | Q(reporter__username__icontains=search)
        )

    total_count = qs.count()
    reports = list(qs[offset : offset + page_size])

    previews = _circle_report_target_previews(reports)
    distinct_counts = _circle_report_distinct_counts(reports)

    summary = CircleReport.objects.aggregate(
        total=Count("id"),
        pending=Count("id", filter=Q(status="pending")),
        resolved_kept=Count("id", filter=Q(status="resolved_kept")),
        resolved_removed=Count("id", filter=Q(status="resolved_removed")),
    )

    return {
        "reports": [
            _circle_report_to_dict(
                r,
                previews.get((r.target_type, str(r.target_id)), _missing_target_preview()),
                distinct_counts.get((r.target_type, str(r.target_id)), 1),
            )
            for r in reports
        ],
        "total_count": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total_count + page_size - 1) // page_size),
        "summary": summary,
    }


def _missing_target_preview() -> dict:
    return {
        "available": False,
        "unavailable_reason": "missing",
        "text": "",
        "media_url": "",
        "media_type": "",
        "thumbnail_url": "",
    }


def _hidden_or_live(target, text: str, media_url: str, media_type: str, thumbnail: str) -> dict:
    if target.deleted_at is not None:
        # Soft-deleted: auto-hidden at the 3-report threshold or removed.
        return {
            "available": False,
            "unavailable_reason": "hidden",
            "text": text,
            "media_url": media_url,
            "media_type": media_type,
            "thumbnail_url": thumbnail,
        }
    return {
        "available": True,
        "unavailable_reason": "",
        "text": text,
        "media_url": media_url,
        "media_type": media_type,
        "thumbnail_url": thumbnail,
    }


def _circle_report_target_previews(reports) -> dict:
    """Bulk-resolve report targets (3 queries max) → {(target_type, id): preview}."""
    from core.circles.models import Anchor, AnchorResponse, Circle

    ids_by_type: dict[str, set[str]] = {"anchor": set(), "response": set(), "circle": set()}
    for r in reports:
        if r.target_type in ids_by_type:
            ids_by_type[r.target_type].add(str(r.target_id))

    previews: dict = {}

    for anchor in Anchor.all_objects.filter(id__in=ids_by_type["anchor"]):
        if anchor.anchor_video:
            media_url, media_type = anchor.anchor_video, "video"
        elif anchor.anchor_image:
            media_url, media_type = anchor.anchor_image, "image"
        else:
            media_url, media_type = anchor.media_url, ""
        previews[("anchor", str(anchor.id))] = _hidden_or_live(
            anchor,
            anchor.title or anchor.content or anchor.anchor_text,
            media_url,
            media_type,
            anchor.anchor_thumbnail,
        )

    for response in AnchorResponse.all_objects.filter(id__in=ids_by_type["response"]):
        previews[("response", str(response.id))] = _hidden_or_live(
            response, response.content, response.media_url, response.media_type, ""
        )

    for circle in Circle.all_objects.filter(id__in=ids_by_type["circle"]):
        previews[("circle", str(circle.id))] = _hidden_or_live(
            circle, circle.name, circle.cover_image, "image", ""
        )

    return previews


def _circle_report_distinct_counts(reports) -> dict:
    """Distinct-reporter count per target — the auto-hide threshold signal."""
    from core.circles.models import CircleReport

    if not reports:
        return {}
    target_q = Q()
    for r in reports:
        target_q |= Q(target_type=r.target_type, target_id=r.target_id)
    rows = (
        CircleReport.objects.filter(target_q)
        .values("target_type", "target_id")
        .annotate(distinct_reporters=Count("reporter_id", distinct=True))
    )
    return {(row["target_type"], str(row["target_id"])): row["distinct_reporters"] for row in rows}


def _circle_report_to_dict(report, preview: dict, distinct_reporters: int) -> dict:
    return {
        "id": str(report.id),
        "reporter_username": getattr(report.reporter, "username", "") or "",
        "circle_id": str(report.circle_id),
        "circle_name": report.circle.name if report.circle_id else "",
        "target_type": report.target_type,
        "target_id": str(report.target_id),
        "reason": report.reason,
        "status": report.status,
        "report_count": distinct_reporters,
        "auto_hidden": preview["unavailable_reason"] == "hidden",
        "content_preview": preview,
        "created_at": report.created_at.isoformat(),
        "resolved_at": report.resolved_at.isoformat() if report.resolved_at else None,
        "resolved_by_username": getattr(report.resolved_by, "username", "") or "",
    }

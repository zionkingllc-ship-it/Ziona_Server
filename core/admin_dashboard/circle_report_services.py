"""Admin listing of circle-content reports (circles.CircleReport).

Circle reports are separate from the global Report table; until now they
were only acted on by the 3-report auto-hide and invisible to admins.
"""

import contextlib

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


VALID_CIRCLE_REPORT_ACTIONS = ("keep", "remove")


def review_circle_report(
    report_id: str,
    action: str,
    admin_user,
    ip_address: str = "",
) -> dict:
    """Resolve a circle-content report.

    - ``keep``   → resolved_kept; auto-hidden anchor/response content is restored.
    - ``remove`` → resolved_removed; the target content is taken down (circles are
      soft-deleted via the standard admin delete flow).

    All *pending* reports on the same target are resolved together with the same
    outcome — circle reports arrive in threes (the auto-hide threshold), and one
    admin decision applies to the target, not a single row.
    """
    from django.db import transaction
    from django.utils import timezone as dj_timezone

    from core.admin_dashboard.permissions import log_admin_action
    from core.circles.models import CircleReport
    from core.shared.exceptions import AdminError, ErrorCode

    if action not in VALID_CIRCLE_REPORT_ACTIONS:
        raise AdminError(
            message=f"Invalid action. Must be one of: {', '.join(VALID_CIRCLE_REPORT_ACTIONS)}.",
            code=ErrorCode.VALIDATION_ERROR,
        )

    with transaction.atomic():
        report = (
            CircleReport.objects.select_for_update(of=("self",))
            .select_related("reporter", "circle", "resolved_by")
            .filter(id=report_id)
            .first()
        )
        if not report:
            raise AdminError(message="Report not found.", code=ErrorCode.REPORT_NOT_FOUND)
        if report.status != "pending":
            raise AdminError(
                message="Report has already been reviewed.",
                code=ErrorCode.REPORT_ALREADY_REVIEWED,
            )

        now = dj_timezone.now()
        if action == "remove":
            _remove_circle_target(report, admin_user, ip_address)
            new_status = "resolved_removed"
        else:
            _restore_circle_target(report)
            new_status = "resolved_kept"

        # One decision per target: resolve every pending sibling report too.
        CircleReport.objects.filter(
            target_type=report.target_type, target_id=report.target_id, status="pending"
        ).update(status=new_status, resolved_at=now, resolved_by=admin_user)

        log_admin_action(
            admin_user=admin_user,
            action="CIRCLE_REPORT_REVIEWED",
            target_type="CircleReport",
            target_id=str(report.id),
            details={
                "action": action,
                "circle_id": str(report.circle_id),
                "report_target_type": report.target_type,
                "report_target_id": str(report.target_id),
            },
            ip_address=ip_address,
        )

        report.refresh_from_db()

    previews = _circle_report_target_previews([report])
    counts = _circle_report_distinct_counts([report])
    key = (report.target_type, str(report.target_id))
    return _circle_report_to_dict(
        report, previews.get(key, _missing_target_preview()), counts.get(key, 1)
    )


def _remove_circle_target(report, admin_user, ip_address: str) -> None:
    """Take down the reported content (mirrors the auto-hide mechanics)."""
    from django.utils import timezone as dj_timezone

    from core.circles.models import Anchor, AnchorResponse
    from core.shared.exceptions import AdminError

    now = dj_timezone.now()
    if report.target_type == "anchor":
        anchor = Anchor.all_objects.filter(id=report.target_id).first()
        if anchor and anchor.deleted_at is None:
            anchor.deleted_at = now
            anchor.save(update_fields=["deleted_at"])
            from core.circles.anchor_services import invalidate_active_anchor_cache

            invalidate_active_anchor_cache(str(anchor.circle_id))
    elif report.target_type == "response":
        response = AnchorResponse.all_objects.filter(id=report.target_id).first()
        if response and response.deleted_at is None:
            response.deleted_at = now
            response.save(update_fields=["deleted_at"])
    elif report.target_type == "circle":
        from core.admin_dashboard.circle_services import CircleManagementService

        # Already-deleted circles raise CIRCLE_NOT_FOUND — nothing left to take
        # down, but the report should still resolve.
        with contextlib.suppress(AdminError):
            CircleManagementService.delete_circle(
                str(report.target_id), admin_user=admin_user, ip_address=ip_address
            )


def _restore_circle_target(report) -> None:
    """Un-hide auto-hidden content the admin decided to keep.

    Circles are never auto-hidden by the report threshold, so a ``keep`` on a
    circle target changes no content — it only resolves the reports.
    """
    from core.circles.models import Anchor, AnchorResponse

    if report.target_type == "anchor":
        anchor = Anchor.all_objects.filter(id=report.target_id).first()
        if anchor and anchor.deleted_at is not None:
            anchor.deleted_at = None
            anchor.save(update_fields=["deleted_at"])
            from core.circles.anchor_services import invalidate_active_anchor_cache

            invalidate_active_anchor_cache(str(anchor.circle_id))
    elif report.target_type == "response":
        response = AnchorResponse.all_objects.filter(id=report.target_id).first()
        if response and response.deleted_at is not None:
            response.deleted_at = None
            response.save(update_fields=["deleted_at"])

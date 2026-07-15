"""GDPR redaction of legacy user snapshots in audit logs.

Split from core/admin_dashboard/user_services.py (no behavior change).
"""

import logging

logger = logging.getLogger("core.admin_dashboard")


def _build_minimal_user_audit_details(user, *, deleted_at) -> dict:
    """Return the minimum deletion audit payload needed for traceability."""
    return {
        "subject_user_id": str(user.id),
        "status_before": user.status,
        "deleted_at": deleted_at.isoformat(),
    }


def redact_legacy_user_snapshot_payloads(*, dry_run: bool = True) -> dict[str, int]:
    """Remove legacy user_snapshot payloads from moderation and audit records."""
    from core.admin_dashboard.models import AdminAuditLog, ModerationAction

    redacted_audit_logs = _redact_json_snapshot_rows(
        AdminAuditLog.objects.order_by("id"),
        field_name="details",
        subject_id_getter=lambda row: row.target_id,
        dry_run=dry_run,
    )
    redacted_moderation_actions = _redact_json_snapshot_rows(
        ModerationAction.objects.select_related("user").order_by("id"),
        field_name="metadata",
        subject_id_getter=lambda row: str(row.user_id),
        dry_run=dry_run,
    )

    return {
        "redacted_audit_logs": redacted_audit_logs,
        "redacted_moderation_actions": redacted_moderation_actions,
    }


def _redact_json_snapshot_rows(
    queryset, *, field_name: str, subject_id_getter, dry_run: bool
) -> int:
    """Strip user_snapshot payloads from JSONField-backed rows."""
    redacted_count = 0

    for row in queryset.iterator():
        payload = getattr(row, field_name, None)
        if not isinstance(payload, dict) or "user_snapshot" not in payload:
            continue

        sanitized_payload = dict(payload)
        sanitized_payload.pop("user_snapshot", None)
        sanitized_payload.setdefault("subject_user_id", subject_id_getter(row))
        sanitized_payload.setdefault("legacy_snapshot_redacted", True)

        redacted_count += 1
        if not dry_run:
            type(row).objects.filter(pk=row.pk).update(**{field_name: sanitized_payload})

    return redacted_count

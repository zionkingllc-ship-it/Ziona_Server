import pytest
from django.utils import timezone

from core.admin_dashboard.models import AdminAuditLog, ModerationAction
from core.admin_dashboard.user_services import (
    UserManagementService,
    redact_legacy_user_snapshot_payloads,
)
from core.users.models import User, UserStatus


@pytest.mark.django_db
def test_list_users(authenticated_admin):
    User.objects.create(email="test2@example.com", username="testuser2", password="password")
    result = UserManagementService.list_users(None, None, 1, 10)
    assert result["total_count"] >= 1
    assert "users" in result


@pytest.mark.django_db
def test_list_users_includes_submitted_report_count(authenticated_admin):
    from core.moderation.models import Report

    reporter = User.objects.create_user(
        email="reporter@example.com",
        username="reporteruser",
        password="Pass123!",
    )
    for i in range(2):
        Report.objects.create(
            target_type="Post",
            target_id=f"1111111{i}-1111-1111-1111-111111111111",
            reason="spam",
            reporter=reporter,
            status="pending",
        )

    result = UserManagementService.list_users(search="reporteruser")
    users = {u["username"]: u for u in result["users"]}
    assert users["reporteruser"]["submitted_reports"] == 2


@pytest.mark.django_db
def test_list_users_includes_all_admin_account_states(authenticated_admin):
    User.objects.create_user(
        email="warned@example.com",
        username="warneduser",
        password="Pass123!",
        status=UserStatus.WARNED,
    )
    User.objects.create_user(
        email="suspended@example.com",
        username="suspendeduser",
        password="Pass123!",
        status=UserStatus.SUSPENDED,
    )
    User.objects.create_user(
        email="inactive@example.com",
        username="inactiveuser",
        password="Pass123!",
        is_active=False,
    )
    deleted = User.objects.create_user(
        email="deleted@example.com",
        username="deleteduser",
        password="Pass123!",
    )
    deleted.deleted_at = timezone.now()
    deleted.is_active = False
    deleted.save(update_fields=["deleted_at", "is_active", "updated_at"])

    result = UserManagementService.list_users("", "", 1, 20)
    states = {user["email"]: user["account_state"] for user in result["users"]}

    assert states["warned@example.com"] == "warned"
    assert states["suspended@example.com"] == "suspended"
    assert states["inactive@example.com"] == "inactive"
    assert states["deleted@example.com"] == "deleted"
    assert result["summary"]["warned"] >= 1
    assert result["summary"]["suspended"] >= 1
    assert result["summary"]["inactive"] >= 1
    assert result["summary"]["deleted"] >= 1


@pytest.mark.django_db
def test_list_users_can_filter_deleted_users(authenticated_admin):
    deleted = User.objects.create_user(
        email="deleted-filter@example.com",
        username="deletedfilter",
        password="Pass123!",
    )
    deleted.deleted_at = timezone.now()
    deleted.is_active = False
    deleted.save(update_fields=["deleted_at", "is_active", "updated_at"])

    result = UserManagementService.list_users("", "deleted", 1, 10)

    assert any(user["email"] == "deleted-filter@example.com" for user in result["users"])


@pytest.mark.django_db
def test_suspend_user(authenticated_admin, create_user):
    user = create_user("suspendme@example.com", "suspendme", "Pass123!", role="user")
    result = UserManagementService.suspend_user(
        str(user.id), "Violation", authenticated_admin["user"]
    )

    user.refresh_from_db()
    assert isinstance(result, dict)
    assert user.status == "suspended"


@pytest.mark.django_db
def test_delete_user_audit_payload_is_sanitized(authenticated_admin, create_user):
    user = create_user("deleteme@example.com", "deleteme", "Pass123!", role="user")

    result = UserManagementService.delete_user(
        str(user.id),
        authenticated_admin["user"],
        ip_address="10.0.0.10",
    )

    user.refresh_from_db()
    moderation_action = ModerationAction.objects.get(user=user)
    audit_log = AdminAuditLog.objects.get(
        action="USER_DELETED",
        target_id=str(user.id),
    )

    assert result["success"] is True
    assert user.deleted_at is not None
    assert "user_snapshot" not in moderation_action.metadata
    assert "user_snapshot" not in audit_log.details
    assert moderation_action.metadata["subject_user_id"] == str(user.id)
    assert audit_log.details["subject_user_id"] == str(user.id)
    assert "email" not in moderation_action.metadata
    assert "username" not in moderation_action.metadata
    assert "full_name" not in moderation_action.metadata


@pytest.mark.django_db
def test_redact_legacy_user_snapshot_payloads_strips_existing_snapshots(
    authenticated_admin,
    create_user,
):
    user = create_user("legacy-snapshot@example.com", "legacy", "Pass123!", role="user")
    legacy_snapshot = {
        "username": user.username,
        "email": user.email,
        "full_name": user.full_name,
    }

    audit_log = AdminAuditLog.objects.create(
        admin_user=authenticated_admin["user"],
        action="USER_DELETED",
        target_type="User",
        target_id=str(user.id),
        details={"user_snapshot": legacy_snapshot},
        ip_address="10.0.0.10",
    )
    moderation_action = ModerationAction.objects.create(
        user=user,
        action_type="deleted",
        reason="Legacy snapshot",
        admin_user=authenticated_admin["user"],
        metadata={"user_snapshot": legacy_snapshot},
    )

    dry_run_result = redact_legacy_user_snapshot_payloads(dry_run=True)
    assert dry_run_result["redacted_audit_logs"] >= 1
    assert dry_run_result["redacted_moderation_actions"] >= 1

    audit_log.refresh_from_db()
    moderation_action.refresh_from_db()
    assert "user_snapshot" in audit_log.details
    assert "user_snapshot" in moderation_action.metadata

    apply_result = redact_legacy_user_snapshot_payloads(dry_run=False)
    assert apply_result["redacted_audit_logs"] >= 1
    assert apply_result["redacted_moderation_actions"] >= 1

    audit_log.refresh_from_db()
    moderation_action.refresh_from_db()
    assert "user_snapshot" not in audit_log.details
    assert "user_snapshot" not in moderation_action.metadata
    assert audit_log.details["subject_user_id"] == str(user.id)
    assert moderation_action.metadata["subject_user_id"] == str(user.id)
    assert audit_log.details["legacy_snapshot_redacted"] is True
    assert moderation_action.metadata["legacy_snapshot_redacted"] is True

import pytest
from django.utils import timezone

from core.admin_dashboard.user_services import UserManagementService
from core.users.models import User, UserStatus


@pytest.mark.django_db
def test_list_users(authenticated_admin):
    User.objects.create(email="test2@example.com", username="testuser2", password="password")
    result = UserManagementService.list_users(None, None, 1, 10)
    assert result["total_count"] >= 1
    assert "users" in result


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

import pytest

from core.admin_dashboard.user_services import UserManagementService
from core.users.models import User


@pytest.mark.django_db
def test_list_users(authenticated_admin):
    User.objects.create(email="test2@example.com", username="testuser2", password="password")
    result = UserManagementService.list_users(None, None, 1, 10)
    assert result["total_count"] >= 1
    assert "users" in result


@pytest.mark.django_db
def test_suspend_user(authenticated_admin, create_user):
    user = create_user("suspendme@example.com", "suspendme", "Pass123!", role="user")
    result = UserManagementService.suspend_user(
        str(user.id), "Violation", authenticated_admin["user"]
    )

    user.refresh_from_db()
    assert isinstance(result, dict)
    assert user.status == "suspended"

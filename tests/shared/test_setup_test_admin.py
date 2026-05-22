import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone

from core.shared.management.commands.setup_test_admin import ADMIN_ACCOUNTS, ADMIN_PASSWORD
from core.users.models import UserRole, UserStatus


@pytest.mark.django_db
def test_setup_test_admin_provisions_all_dashboard_admins():
    call_command("setup_test_admin")

    user_model = get_user_model()

    for account in ADMIN_ACCOUNTS:
        user = user_model.objects.get(email=account["email"])
        assert user.check_password(ADMIN_PASSWORD)
        assert user.role == UserRole.ADMIN
        assert user.status == UserStatus.ACTIVE
        assert user.is_staff is True
        assert user.is_superuser is True
        assert user.is_active is True
        assert user.is_email_verified is True
        assert user.deleted_at is None


@pytest.mark.django_db
def test_setup_test_admin_repairs_existing_deleted_or_suspended_admin_account():
    user_model = get_user_model()
    email = "info@zionking.org"
    user = user_model.all_objects.create(
        email=email,
        username="existing_info_user",
        role=UserRole.USER,
        status=UserStatus.SUSPENDED,
        is_staff=False,
        is_superuser=False,
        is_active=False,
        is_email_verified=False,
        deleted_at=timezone.now(),
        suspended_at=timezone.now(),
        suspension_reason="Old suspension",
    )
    user.set_password("OldPassword123!")
    user.save(update_fields=["password"])

    call_command("setup_test_admin")

    user.refresh_from_db()
    assert user.check_password(ADMIN_PASSWORD)
    assert user.role == UserRole.ADMIN
    assert user.status == UserStatus.ACTIVE
    assert user.is_staff is True
    assert user.is_superuser is True
    assert user.is_active is True
    assert user.is_email_verified is True
    assert user.deleted_at is None
    assert user.suspended_at is None
    assert user.suspension_reason == ""

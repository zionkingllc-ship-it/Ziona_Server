from datetime import timedelta

import pytest
from django.utils import timezone

from core.admin_dashboard.circle_services import CircleManagementService
from core.circles.models import Circle
from core.shared.exceptions import AdminError


@pytest.mark.django_db
def test_create_circle(authenticated_admin):
    circle = CircleManagementService.create_circle(
        name="Test Circle",
        description="Test description",
        cover_image="http://example.com/cover.jpg",
        profile_image_url="http://example.com/profile.jpg",
        admin_user=authenticated_admin["user"],
    )
    assert circle["name"] == "Test Circle"
    assert circle["status"] == "active"


@pytest.mark.django_db
def test_edit_circle_cooldown(authenticated_admin):
    # Cooldown test limit
    circle = Circle.objects.create(
        name="Old Circle",
        description="Old",
        created_by=authenticated_admin["user"],
        last_edited_at=timezone.now() - timedelta(days=10),  # Less than 60 days
    )

    with pytest.raises(AdminError) as exc_info:
        CircleManagementService.edit_circle(
            str(circle.id), authenticated_admin["user"], name="New Name"
        )

    assert exc_info.value.code == "CIRCLE_EDIT_COOLDOWN"

    # valid cooldown
    circle.last_edited_at = timezone.now() - timedelta(days=61)
    circle.save()
    updated = CircleManagementService.edit_circle(
        str(circle.id), authenticated_admin["user"], name="New Name"
    )
    assert updated["name"] == "New Name"

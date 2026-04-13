from datetime import timedelta

import pytest
from django.utils import timezone

from core.admin_dashboard.anchor_services import AnchorManagementService
from core.circles.models import Anchor, Circle


@pytest.mark.django_db
def test_schedule_anchor(authenticated_admin):
    circle = Circle.objects.create(name="Anchor Circle", created_by=authenticated_admin["user"])
    anchor = Anchor.objects.create(
        circle=circle,
        anchor_type="TEXT",
        content="Test anchor",
        anchor_status="draft",
        published_at=timezone.now(),
        expires_at=timezone.now() + timedelta(days=1),
    )
    scheduled_for = timezone.now() + timedelta(days=1)

    updated_anchor = AnchorManagementService.schedule_anchor(
        str(anchor.id), scheduled_for, authenticated_admin["user"]
    )
    assert updated_anchor["anchor_status"] == "scheduled"
    assert "scheduled_for" in updated_anchor


@pytest.mark.django_db
def test_send_now_anchor(authenticated_admin):
    circle = Circle.objects.create(name="Anchor Circle 2", created_by=authenticated_admin["user"])
    anchor = Anchor.objects.create(
        circle=circle,
        anchor_type="TEXT",
        content="Test anchor now",
        anchor_status="draft",
        published_at=timezone.now(),
        expires_at=timezone.now() + timedelta(days=1),
    )

    updated_anchor = AnchorManagementService.send_now(str(anchor.id), authenticated_admin["user"])
    assert updated_anchor["anchor_status"] == "posted"
    assert updated_anchor["posted_at"] is not None

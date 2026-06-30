from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from core.admin_dashboard.anchor_services import AnchorManagementService
from core.circles.anchor_services import create_anchor as create_public_anchor
from core.circles.anchor_services import get_active_anchor
from core.circles.models import Anchor, Circle, CircleMembership


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


@pytest.mark.django_db
def test_create_anchor_accepts_image_and_video(authenticated_admin):
    circle = Circle.objects.create(
        name="Media Anchor Circle",
        description="Circle with media anchors",
        cover_image="https://example.com/cover.jpg",
        created_by=authenticated_admin["user"],
    )

    anchor = AnchorManagementService.create_anchor(
        circle_id=str(circle.id),
        anchor_type="video",
        title="Video with cover",
        anchor_image="https://example.com/cover-image.jpg",
        anchor_video="https://example.com/anchor-video.mp4",
        anchor_thumbnail="https://example.com/thumb.jpg",
        admin_user=authenticated_admin["user"],
    )

    assert anchor["media_url"] == "https://example.com/anchor-video.mp4"
    assert anchor["anchor_image"] == "https://example.com/cover-image.jpg"
    assert anchor["anchor_video"] == "https://example.com/anchor-video.mp4"
    assert anchor["anchor_thumbnail"] == "https://example.com/thumb.jpg"


@pytest.mark.django_db
def test_create_anchor_keeps_legacy_media_url_fallback(authenticated_admin):
    circle = Circle.objects.create(
        name="Legacy Media Anchor Circle",
        description="Circle with legacy media anchors",
        cover_image="https://example.com/cover.jpg",
        created_by=authenticated_admin["user"],
    )

    anchor = AnchorManagementService.create_anchor(
        circle_id=str(circle.id),
        anchor_type="image",
        title="Image anchor",
        media_url="https://example.com/image.jpg",
        admin_user=authenticated_admin["user"],
    )

    assert anchor["media_url"] == "https://example.com/image.jpg"
    assert anchor["anchor_image"] == "https://example.com/image.jpg"
    assert anchor["anchor_video"] == ""


@pytest.mark.django_db
def test_admin_create_anchor_mutation_accepts_typed_media(
    api_client,
    authenticated_admin,
):
    circle = Circle.objects.create(
        name="GraphQL Media Anchor Circle",
        description="Circle with GraphQL media anchors",
        cover_image="https://example.com/cover.jpg",
        created_by=authenticated_admin["user"],
    )
    mutation = f"""
    mutation {{
      adminCreateAnchor(
        circleId: "{circle.id}",
        anchorType: "video",
        title: "Video and image",
        anchorImage: "https://example.com/image.jpg",
        anchorVideo: "https://example.com/video.mp4",
        anchorThumbnail: "https://example.com/thumb.jpg"
      ) {{
        success
        anchor {{
          mediaUrl
          anchorImage
          anchorVideo
          anchorThumbnail
        }}
        error {{
          code
          message
        }}
      }}
    }}
    """
    headers = {"HTTP_AUTHORIZATION": f"Bearer {authenticated_admin['access_token']}"}

    response = api_client.post(
        "/graphql/", {"query": mutation}, content_type="application/json", **headers
    )
    data = response.json()

    assert "errors" not in data, data.get("errors")
    result = data["data"]["adminCreateAnchor"]
    assert result["success"] is True, result
    assert result["anchor"]["mediaUrl"] == "https://example.com/video.mp4"
    assert result["anchor"]["anchorImage"] == "https://example.com/image.jpg"
    assert result["anchor"]["anchorVideo"] == "https://example.com/video.mp4"
    assert result["anchor"]["anchorThumbnail"] == "https://example.com/thumb.jpg"


@pytest.mark.django_db
def test_public_create_anchor_schedules_future_anchor_without_replacing_active(authenticated_admin):
    admin = authenticated_admin["user"]
    circle = Circle.objects.create(
        name="Scheduled Preview Circle",
        description="Scheduling coverage",
        cover_image="https://example.com/cover.jpg",
        created_by=admin,
    )
    CircleMembership.objects.create(circle=circle, user=admin, role="admin")
    now = timezone.now()
    active_anchor = Anchor.objects.create(
        circle=circle,
        created_by=admin,
        anchor_type="text",
        title="Active anchor",
        content="Still active",
        anchor_status="posted",
        published_at=now,
        posted_at=now,
        expires_at=now + timedelta(hours=24),
    )
    scheduled_for = now + timedelta(days=2)

    with patch("core.admin_dashboard.tasks.post_scheduled_anchor.apply_async") as apply_async:
        apply_async.return_value.id = "scheduled-anchor-task"
        scheduled_anchor = create_public_anchor(
            creator_id=str(admin.id),
            circle_id=str(circle.id),
            anchor_type="text",
            title="Future anchor",
            content="Do not publish yet",
            published_at=scheduled_for,
        )

    scheduled_anchor.refresh_from_db()
    assert scheduled_anchor.anchor_status == "scheduled"
    assert scheduled_anchor.scheduled_for == scheduled_for
    assert scheduled_anchor.posted_at is None
    assert scheduled_anchor.celery_task_id == "scheduled-anchor-task"
    assert get_active_anchor(str(circle.id), viewer_id=str(admin.id)).id == active_anchor.id
    apply_async.assert_called_once()

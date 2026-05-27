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

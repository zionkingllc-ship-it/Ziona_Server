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


@pytest.mark.django_db
def test_admin_create_circle_mutation(api_client, authenticated_admin):
    mutation = """
    mutation {
        adminCreateCircle(
            name: "GraphQL Test Circle",
            description: "A circle created via GraphQL mutation",
            coverImage: "http://example.com/graphql-cover.jpg",
            profileImageUrl: "http://example.com/graphql-profile.jpg"
        ) {
            success
            circle {
                id
                name
                description
                coverImage
                profileImageUrl
                status
                isActive
            }
            error {
                code
                message
            }
        }
    }
    """
    headers = {"HTTP_AUTHORIZATION": f"Bearer {authenticated_admin['access_token']}"}
    response = api_client.post(
        "/graphql/", {"query": mutation}, content_type="application/json", **headers
    )
    data = response.json()
    assert "errors" not in data, data.get("errors")
    result = data["data"]["adminCreateCircle"]
    assert result["success"] is True
    assert result["circle"]["name"] == "GraphQL Test Circle"
    assert result["circle"]["status"] == "active"
    assert result["circle"]["isActive"] is True

    # Try to create a circle with the same name (should fail gracefully with DUPLICATE_NAME)
    response_dup = api_client.post(
        "/graphql/", {"query": mutation}, content_type="application/json", **headers
    )
    data_dup = response_dup.json()
    assert "errors" not in data_dup, data_dup.get("errors")
    result_dup = data_dup["data"]["adminCreateCircle"]
    assert result_dup["success"] is False
    assert result_dup["circle"] is None
    assert result_dup["error"]["code"] == "DUPLICATE_NAME"

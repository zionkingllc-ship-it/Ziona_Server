from datetime import timedelta

import pytest
from django.utils import timezone

from core.admin_dashboard.circle_services import CircleManagementService
from core.circles.models import Anchor, Circle, CircleMembership, CirclePost
from core.shared.exceptions import AdminError


@pytest.mark.django_db
def test_create_circle(authenticated_admin):
    circle = CircleManagementService.create_circle(
        name="Test Circle",
        description="Test description",
        cover_image="http://example.com/cover.jpg",
        profile_image_url="http://example.com/profile.jpg",
        banner_image="http://example.com/banner.jpg",
        admin_user=authenticated_admin["user"],
    )
    assert circle["name"] == "Test Circle"
    assert circle["status"] == "active"
    assert circle["banner_image"] == "http://example.com/banner.jpg"


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
            profileImageUrl: "http://example.com/graphql-profile.jpg",
            bannerImage: "http://example.com/graphql-banner.jpg"
        ) {
            success
            circle {
                id
                name
                description
                coverImage
                profileImageUrl
                bannerImage
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
    assert result["circle"]["bannerImage"] == "http://example.com/graphql-banner.jpg"
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


@pytest.mark.django_db
def test_get_circle_stats_is_scoped_to_requested_circle(authenticated_admin, create_user):
    admin = authenticated_admin["user"]
    member = create_user(email="member@example.com", username="memberuser")
    other_member = create_user(email="other-member@example.com", username="othermember")
    now = timezone.now()

    circle = Circle.objects.create(
        name="Scoped Circle",
        description="Stats should be scoped here",
        created_by=admin,
    )
    other_circle = Circle.objects.create(
        name="Other Circle",
        description="Should not affect scoped stats",
        created_by=admin,
    )

    CircleMembership.objects.create(circle=circle, user=admin, role="admin")
    CircleMembership.objects.create(circle=circle, user=member, role="member")
    CircleMembership.objects.create(circle=other_circle, user=other_member, role="member")

    Anchor.objects.create(
        circle=circle,
        created_by=admin,
        anchor_type="text",
        title="Scoped Anchor",
        content="Scoped content",
        prayed_count=5,
        anchor_liked_count=6,
        published_at=now,
        expires_at=now + timedelta(hours=24),
    )
    Anchor.objects.create(
        circle=other_circle,
        created_by=admin,
        anchor_type="text",
        title="Other Anchor",
        content="Other content",
        prayed_count=50,
        anchor_liked_count=60,
        published_at=now,
        expires_at=now + timedelta(hours=24),
    )

    CirclePost.objects.create(
        circle=circle,
        user=member,
        text="Scoped post",
        likes_count=3,
        comments_count=2,
        prayed_count=1,
        anchor_liked_count=4,
    )
    CirclePost.objects.create(
        circle=other_circle,
        user=other_member,
        text="Other post",
        likes_count=30,
        comments_count=20,
        prayed_count=10,
        anchor_liked_count=40,
    )

    stats = CircleManagementService.get_circle_stats(str(circle.id))

    assert stats["member_count"] == 2
    assert stats["anchor_count"] == 1
    assert stats["engagement"]["value"] == 21
    assert stats["engagement"]["label"] == "Total Engagement"


@pytest.mark.django_db
def test_admin_circle_stats_query(api_client, authenticated_admin, create_user):
    admin = authenticated_admin["user"]
    member = create_user(email="stats-member@example.com", username="statsmember")
    now = timezone.now()
    circle = Circle.objects.create(
        name="GraphQL Stats Circle",
        description="Circle detail stats",
        created_by=admin,
    )
    CircleMembership.objects.create(circle=circle, user=admin, role="admin")
    CircleMembership.objects.create(circle=circle, user=member, role="member")
    Anchor.objects.create(
        circle=circle,
        created_by=admin,
        anchor_type="text",
        title="GraphQL Anchor",
        content="Anchor content",
        prayed_count=2,
        anchor_liked_count=3,
        published_at=now,
        expires_at=now + timedelta(hours=24),
    )
    CirclePost.objects.create(
        circle=circle,
        user=member,
        text="GraphQL post",
        likes_count=4,
        comments_count=5,
        prayed_count=6,
        anchor_liked_count=7,
    )

    query = """
    query AdminCircleStats($circleId: String!) {
        adminCircleStats(circleId: $circleId) {
            success
            stats {
                memberCount
                anchorCount
                engagement {
                    value
                    change
                    label
                }
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
        "/graphql/",
        {"query": query, "variables": {"circleId": str(circle.id)}},
        content_type="application/json",
        **headers,
    )
    data = response.json()

    assert "errors" not in data, data.get("errors")
    result = data["data"]["adminCircleStats"]
    assert result["success"] is True
    assert result["stats"]["memberCount"] == 2
    assert result["stats"]["anchorCount"] == 1
    assert result["stats"]["engagement"]["value"] == 27
    assert result["stats"]["engagement"]["label"] == "Total Engagement"

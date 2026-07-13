import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from core.admin_dashboard.circle_services import CircleManagementService
from core.admin_dashboard.models import AdminAuditLog
from core.circles.models import Anchor, Circle, CircleMembership, CirclePost
from core.shared.exceptions import AdminError, ErrorCode


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
def test_edit_circle_has_no_cooldown(authenticated_admin):
    # Circles were previously locked for 60 days after an edit. That cooldown
    # has been removed — admins can edit at any time, even right after a prior edit.
    circle = Circle.objects.create(
        name="Old Circle",
        description="Old",
        created_by=authenticated_admin["user"],
        last_edited_at=timezone.now() - timedelta(days=1),  # Edited yesterday
    )

    updated = CircleManagementService.edit_circle(
        str(circle.id), authenticated_admin["user"], name="New Name"
    )
    assert updated["name"] == "New Name"


@pytest.mark.django_db
def test_list_circle_members_includes_email(authenticated_admin, create_user):
    circle = Circle.objects.create(
        name="Members Circle",
        description="With members",
        created_by=authenticated_admin["user"],
    )
    member = create_user(email="member@example.com", username="memberuser")
    CircleMembership.objects.create(circle=circle, user=member, role="member")

    result = CircleManagementService.list_circle_members(str(circle.id))

    assert result["total_count"] == 1
    assert result["members"][0]["email"] == "member@example.com"


@pytest.mark.django_db
def test_delete_circle_soft_deletes(authenticated_admin):
    circle = Circle.objects.create(
        name="Delete Me",
        description="Soft delete this circle",
        cover_image="https://example.com/cover.jpg",
        created_by=authenticated_admin["user"],
    )

    result = CircleManagementService.delete_circle(
        str(circle.id),
        admin_user=authenticated_admin["user"],
    )

    circle.refresh_from_db()
    assert result["status"] == "inactive"
    assert result["is_active"] is False
    assert circle.deleted_at is not None
    assert Circle.objects.filter(id=circle.id, deleted_at__isnull=True).exists() is False


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
def test_admin_delete_circle_mutation(api_client, authenticated_admin):
    circle = Circle.objects.create(
        name="GraphQL Delete Circle",
        description="Delete via mutation",
        cover_image="https://example.com/cover.jpg",
        created_by=authenticated_admin["user"],
    )

    mutation = f"""
    mutation {{
        adminDeleteCircle(circleId: "{circle.id}") {{
            success
            circle {{
                id
                status
                isActive
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
    result = data["data"]["adminDeleteCircle"]
    assert result["success"] is True
    assert result["circle"]["status"] == "inactive"
    assert result["circle"]["isActive"] is False

    circle.refresh_from_db()
    assert circle.deleted_at is not None


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


@pytest.mark.django_db
def test_deactivate_then_activate_circle_service(authenticated_admin):
    admin = authenticated_admin["user"]
    circle = Circle.objects.create(
        name="Toggle Me",
        description="Deactivate then reactivate",
        cover_image="https://example.com/cover.jpg",
        created_by=admin,
    )

    deactivated = CircleManagementService.deactivate_circle(str(circle.id), admin_user=admin)
    assert deactivated["status"] == "inactive"
    assert deactivated["is_active"] is False

    reactivated = CircleManagementService.activate_circle(str(circle.id), admin_user=admin)
    assert reactivated["status"] == "active"
    assert reactivated["is_active"] is True

    circle.refresh_from_db()
    assert circle.status == "active"
    assert circle.is_active is True

    assert AdminAuditLog.objects.filter(
        action="CIRCLE_ACTIVATED", target_id=str(circle.id)
    ).exists()


@pytest.mark.django_db
def test_admin_activate_circle_mutation(api_client, authenticated_admin):
    admin = authenticated_admin["user"]
    circle = Circle.objects.create(
        name="Reactivate Via Mutation",
        description="Reactivate via GraphQL",
        cover_image="https://example.com/cover.jpg",
        created_by=admin,
    )
    CircleManagementService.deactivate_circle(str(circle.id), admin_user=admin)

    mutation = f"""
    mutation {{
        adminActivateCircle(circleId: "{circle.id}") {{
            success
            circle {{
                id
                status
                isActive
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
    result = data["data"]["adminActivateCircle"]
    assert result["success"] is True
    assert result["circle"]["status"] == "active"
    assert result["circle"]["isActive"] is True

    circle.refresh_from_db()
    assert circle.is_active is True


@pytest.mark.django_db
def test_admin_deactivate_circle_mutation(api_client, authenticated_admin):
    circle = Circle.objects.create(
        name="Deactivate Via Mutation",
        description="Deactivate via GraphQL",
        cover_image="https://example.com/cover.jpg",
        created_by=authenticated_admin["user"],
    )

    mutation = f"""
    mutation {{
        adminDeactivateCircle(circleId: "{circle.id}") {{
            success
            circle {{
                id
                status
                isActive
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
    result = data["data"]["adminDeactivateCircle"]
    assert result["success"] is True
    assert result["circle"]["status"] == "inactive"
    assert result["circle"]["isActive"] is False

    circle.refresh_from_db()
    assert circle.is_active is False


@pytest.mark.django_db
def test_activate_circle_not_found(authenticated_admin):
    with pytest.raises(AdminError) as exc:
        CircleManagementService.activate_circle(
            str(uuid.uuid4()), admin_user=authenticated_admin["user"]
        )
    assert exc.value.code == ErrorCode.CIRCLE_NOT_FOUND

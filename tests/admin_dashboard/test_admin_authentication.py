import pytest

from core.admin_dashboard.models import AdminAuditLog


@pytest.mark.django_db
def test_admin_login_success(api_client, create_admin_user):
    create_admin_user(password="AdminPass123!")
    query = """
    mutation {
        adminLogin(email: "admin@example.com", password: "AdminPass123!") {
            accessToken
            refreshToken
        }
    }
    """
    response = api_client.post("/graphql/", {"query": query}, content_type="application/json")
    data = response.json()

    assert "errors" not in data, data["errors"]
    assert AdminAuditLog.objects.count() == 1
    assert AdminAuditLog.objects.first().action == "ADMIN_LOGIN"


@pytest.mark.django_db
def test_admin_login_normal_user(api_client, create_user):
    create_user(email="user@example.com", password="UserPass123!", role="user")
    query = """
    mutation {
        adminLogin(email: "user@example.com", password: "UserPass123!") {
            error {
                code
            }
        }
    }
    """
    response = api_client.post("/graphql/", {"query": query}, content_type="application/json")
    data = response.json()

    assert data["data"]["adminLogin"]["error"]["code"] == "NOT_AUTHORIZED"

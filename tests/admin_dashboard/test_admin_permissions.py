from unittest.mock import MagicMock

import pytest

from core.admin_dashboard.permissions import admin_required


@pytest.mark.django_db
def test_admin_required_success(authenticated_admin):
    @admin_required
    def dummy_resolver(root, info, **kwargs):
        return "Success!"

    request_mock = MagicMock()
    request_mock.META = {"HTTP_AUTHORIZATION": f"Bearer {authenticated_admin['access_token']}"}
    request_mock.META["REMOTE_ADDR"] = "127.0.0.1"

    info = MagicMock()
    # Mock context as an object with 'request' attribute
    info.context = MagicMock()
    info.context.request = request_mock

    result = dummy_resolver(None, info=info)
    assert result == "Success!"


@pytest.mark.django_db
def test_admin_required_forbidden(create_user):
    user = create_user("basic@example.com", "basic", "Pass123!", role="user")
    from core.authentication.tokens import TokenService

    token = TokenService.generate_access_token(str(user.id), user.role)

    @admin_required
    def dummy_resolver(root, info, **kwargs):
        return "Success!"

    request_mock = MagicMock()
    request_mock.META = {"HTTP_AUTHORIZATION": f"Bearer {token}"}
    request_mock.META["REMOTE_ADDR"] = "127.0.0.1"

    info = MagicMock()
    # Mock context as an object with 'request' attribute
    info.context = MagicMock()
    info.context.request = request_mock

    with pytest.raises(PermissionError) as exc_info:
        dummy_resolver(None, info=info)

    assert "Admin access required" in str(exc_info.value)

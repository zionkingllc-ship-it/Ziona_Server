from unittest.mock import patch

import pytest
from django.test import Client

pytestmark = pytest.mark.django_db


@pytest.fixture
def api_client() -> Client:
    """Client configured to not raise exceptions directly so middleware can catch them."""
    return Client(raise_request_exception=False)


def test_unhandled_exception_returns_json(api_client: Client, settings):
    settings.DEBUG = False
    with patch(
        "core.authentication.views.MeView.get", side_effect=ValueError("TEST ERROR FOR DEBUGGING")
    ):
        response = api_client.get("/api/auth/me")

    data = response.json()
    assert "success" in data
    assert data["success"] is False
    assert "error" in data
    assert data["error"]["code"] == "INTERNAL_SERVER_ERROR"


def test_exception_returns_status_500(api_client: Client, settings):
    settings.DEBUG = False
    with patch(
        "core.authentication.views.MeView.get", side_effect=ValueError("TEST ERROR FOR DEBUGGING")
    ):
        response = api_client.get("/api/auth/me")
    assert response.status_code == 500


def test_content_type_is_json(api_client: Client, settings):
    settings.DEBUG = False
    with patch(
        "core.authentication.views.MeView.get", side_effect=ValueError("TEST ERROR FOR DEBUGGING")
    ):
        response = api_client.get("/api/auth/me")
    assert response.headers.get("Content-Type") == "application/json"


def test_debug_mode_includes_traceback(api_client: Client, settings):
    settings.DEBUG = True
    with patch(
        "core.authentication.views.MeView.get", side_effect=ValueError("TEST ERROR FOR DEBUGGING")
    ):
        response = api_client.get("/api/auth/me")

    data = response.json()
    error_detail = data["error"]

    assert "type" in error_detail
    assert error_detail["type"] == "ValueError"
    assert "TEST ERROR FOR DEBUGGING" in error_detail["message"]
    assert "traceback" in error_detail
    assert isinstance(error_detail["traceback"], list)
    assert len(error_detail["traceback"]) > 0


def test_production_mode_hides_details(api_client: Client, settings):
    settings.DEBUG = False
    with patch(
        "core.authentication.views.MeView.get", side_effect=ValueError("TEST ERROR FOR DEBUGGING")
    ):
        response = api_client.get("/api/auth/me")

    data = response.json()
    error_detail = data["error"]

    assert "type" not in error_detail
    assert "traceback" not in error_detail
    assert error_detail["message"] == "An internal error occurred. Please try again later."


def test_404_handler_returns_json(api_client: Client):
    response = api_client.get("/api/invalid-route")

    assert response.status_code == 404
    assert response.headers.get("Content-Type") == "application/json"

    data = response.json()
    assert data["success"] is False
    assert data["error"]["code"] == "NOT_FOUND"
    assert data["error"]["message"] == "Endpoint not found"
    assert data["error"]["path"] == "/api/invalid-route"


def test_exception_logged_with_traceback(api_client: Client, settings):
    settings.DEBUG = False
    with patch("core.shared.middleware.logger.error") as mock_logger:
        with patch(
            "core.authentication.views.MeView.get",
            side_effect=ValueError("TEST ERROR FOR DEBUGGING"),
        ):
            api_client.get("/api/auth/me")

        mock_logger.assert_called_once()
        args, kwargs = mock_logger.call_args

        assert "Unhandled exception in request" in args[0]
        assert kwargs.get("exc_info") is True
        assert "extra" in kwargs

        extra = kwargs["extra"]
        assert extra["path"] == "/api/auth/me"
        assert extra["method"] == "GET"
        assert extra["exception_type"] == "ValueError"
        assert "user_id" in extra

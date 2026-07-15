from unittest.mock import patch

import pytest
from django.test import Client, override_settings
from django.urls import reverse

from core.users.models import User

pytestmark = pytest.mark.django_db


@pytest.fixture
def client() -> Client:
    return Client()


@pytest.fixture
def run_rate_limited():
    from django.core.cache import cache

    # Clear cache before testing rate limit
    cache.clear()
    yield
    cache.clear()


def test_check_email_success_true(client: Client) -> None:
    User.objects.create_user(
        email="registered@example.com",
        username="testuser",
        password="password123",
        is_email_verified=True,
    )

    response = client.post(
        reverse("authentication:check-email"),
        {"email": "registered@example.com"},
        content_type="application/json",
    )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["data"]["exists"] is True


def test_check_email_success_false(client: Client) -> None:
    response = client.post(
        reverse("authentication:check-email"),
        {"email": "unregistered@example.com"},
        content_type="application/json",
    )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["data"]["exists"] is False


def test_check_email_unverified_returns_false(client: Client) -> None:
    User.objects.create_user(
        email="unverified_check@example.com",
        username="unverified",
        password="password123",
        is_email_verified=False,
    )

    response = client.post(
        reverse("authentication:check-email"),
        {"email": "unverified_check@example.com"},
        content_type="application/json",
    )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    # Unverified emails should appear as entirely unregistered to the mobile frontend
    assert data["data"]["exists"] is False


def test_check_email_missing_email(client: Client) -> None:
    response = client.post(
        reverse("authentication:check-email"),
        {},
        content_type="application/json",
    )

    assert response.status_code == 400
    data = response.json()
    assert data["success"] is False
    assert data["error"]["code"] == "MISSING_FIELDS"


def test_check_email_invalid_email_format(client: Client) -> None:
    response = client.post(
        reverse("authentication:check-email"),
        {"email": "invalidemail"},
        content_type="application/json",
    )

    assert response.status_code == 400
    data = response.json()
    assert data["success"] is False
    assert data["error"]["code"] == "INVALID_EMAIL"


def test_check_email_normalization(client: Client) -> None:
    User.objects.create_user(
        email="normalized@example.com",
        username="testuser",
        password="password123",
        is_email_verified=True,
    )

    response = client.post(
        reverse("authentication:check-email"),
        {"email": "  NORMALIZED@example.com  "},
        content_type="application/json",
    )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["data"]["exists"] is True


@override_settings(RATE_LIMIT_ENABLED=True)
def test_check_email_rate_limit(client: Client) -> None:
    url = reverse("authentication:check-email")

    # Patch the LuaLimiter that the refactored middleware delegates to.
    # Return (is_limited=True, retry_after=60) to simulate an exceeded limit.
    with patch("core.shared.redis_lua.LuaLimiter.check_rate_limit", return_value=(True, 60)):
        response = client.post(url, {"email": "test@example.com"}, content_type="application/json")

        assert response.status_code == 429
        data = response.json()
        assert data["success"] is False
        assert data["retryAfter"] == 60
        assert data["userMessage"] == "Too many requests. Please try again in about a minute."
        assert data["error"]["code"] == "RATE_LIMIT_EXCEEDED"
        assert data["error"]["details"]["retryAfter"] == 60
        assert response.headers["Retry-After"] == "60"

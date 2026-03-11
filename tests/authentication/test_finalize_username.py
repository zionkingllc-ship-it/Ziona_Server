from unittest.mock import patch

import pytest
from django.conf import settings
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from core.authentication.services import AuthService
from core.users.models import User

pytestmark = pytest.mark.django_db


@pytest.fixture
def client() -> Client:
    return Client()


@pytest.fixture
def user() -> User:
    return User.objects.create_user(
        email="testfinalize@example.com",
        username="temp_user_123",
        password="password123",
        needs_username_selection=True,
    )


@pytest.fixture
def access_token(user: User) -> str:
    from core.authentication.tokens import TokenService

    return TokenService.generate_access_token(str(user.id), user.role)


@patch("google.oauth2.id_token.verify_oauth2_token")
def test_oauth_new_user_creates_temp_username(mock_verify, client: Client) -> None:
    mock_verify.return_value = {
        "sub": "google_uid_1",
        "email": "new.oauth1@gmail.com",
        "name": "New OAuth User 1",
        "picture": "http://example.com/pic.jpg",
        "aud": settings.GOOGLE_CLIENT_ID,
    }
    url = reverse("authentication:google-oauth")
    response = client.post(url, {"id_token": "fake_token"}, content_type="application/json")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["user"]["username"].startswith("user_")
    assert data["user"]["needsUsernameSelection"] is True
    assert data["isNewUser"] is True


@patch("google.oauth2.id_token.verify_oauth2_token")
def test_oauth_new_user_sets_needs_username_selection_flag(mock_verify, client: Client) -> None:
    mock_verify.return_value = {
        "sub": "google_uid_2",
        "email": "new.oauth2@gmail.com",
        "name": "New OAuth User 2",
        "aud": settings.GOOGLE_CLIENT_ID,
    }
    url = reverse("authentication:google-oauth")
    client.post(url, {"id_token": "fake_token"}, content_type="application/json")
    user = User.objects.get(email="new.oauth2@gmail.com")
    assert user.needs_username_selection is True


@patch("google.oauth2.id_token.verify_oauth2_token")
def test_oauth_response_includes_needs_username_selection_field(
    mock_verify, client: Client
) -> None:
    mock_verify.return_value = {
        "sub": "google_uid_3",
        "email": "new.oauth3@gmail.com",
        "name": "New OAuth User 3",
        "aud": settings.GOOGLE_CLIENT_ID,
    }
    url = reverse("authentication:google-oauth")
    response = client.post(url, {"id_token": "fake_token"}, content_type="application/json")
    data = response.json()["data"]["user"]
    assert "needsUsernameSelection" in data
    assert data["needsUsernameSelection"] is True


@patch("google.oauth2.id_token.verify_oauth2_token")
def test_oauth_existing_user_keeps_username_and_flag_false(mock_verify, client: Client) -> None:
    User.objects.create_user(
        email="existing.oauth@gmail.com",
        username="existingUser",
        google_id="google_uid_4",
        social_auth_provider="google",
        needs_username_selection=False,
    )
    mock_verify.return_value = {
        "sub": "google_uid_4",
        "email": "existing.oauth@gmail.com",
        "name": "Existing OAuth User",
        "aud": settings.GOOGLE_CLIENT_ID,
    }
    url = reverse("authentication:google-oauth")
    response = client.post(url, {"id_token": "fake_token"}, content_type="application/json")
    data = response.json()["data"]
    assert data["user"]["username"] == "existingUser"
    assert data["user"]["needsUsernameSelection"] is False
    assert data["isNewUser"] is False


def test_suggest_usernames_works_without_dob() -> None:
    suggestions = AuthService.suggest_usernames("jane.doe@gmail.com")
    assert len(suggestions) == 4
    for item in suggestions:
        assert item.startswith("jane")


def test_suggest_usernames_uses_random_digits_for_oauth() -> None:
    suggestions = AuthService.suggest_usernames("samuel@gmail.com")
    assert len(suggestions) == 4
    assert any(any(c.isdigit() for c in s) for s in suggestions)


def test_suggest_usernames_returns_four_unique_suggestions() -> None:
    User.objects.create_user(email="taken1@gmail.com", username="samue11")
    User.objects.create_user(email="taken2@gmail.com", username="samue12")
    suggestions = AuthService.suggest_usernames("samuel@gmail.com")
    assert len(suggestions) == 4
    assert len(set(suggestions)) == 4


def test_finalize_username_updates_username(client: Client, user: User, access_token: str) -> None:
    url = reverse("authentication:finalize_username")
    response = client.post(
        url,
        {"username": "new_samuel24"},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {access_token}",
    )
    assert response.status_code == 200
    user.refresh_from_db()
    assert user.username == "new_samuel24"


def test_finalize_username_clears_needs_username_selection_flag(
    client: Client, user: User, access_token: str
) -> None:
    url = reverse("authentication:finalize_username")
    response = client.post(
        url,
        {"username": "new_samuel25"},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {access_token}",
    )
    assert response.status_code == 200
    user.refresh_from_db()
    assert user.needs_username_selection is False


def test_finalize_username_invalidates_user_me_cache(
    client: Client, user: User, access_token: str
) -> None:
    cache_key = f"user_me_data_{user.id}"
    cache.set(cache_key, {"some": "data"})
    assert cache.get(cache_key) is not None

    url = reverse("authentication:finalize_username")
    response = client.post(
        url,
        {"username": "new_samuel26"},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {access_token}",
    )

    assert response.status_code == 200
    assert cache.get(cache_key) is None


def test_finalize_username_requires_authentication(client: Client, user: User) -> None:
    url = reverse("authentication:finalize_username")
    response = client.post(url, {"username": "new_samuel26"}, content_type="application/json")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def test_finalize_username_validates_username_availability(
    client: Client, user: User, access_token: str
) -> None:
    User.objects.create_user(email="taken@example.com", username="taken_name", password="123")
    url = reverse("authentication:finalize_username")
    response = client.post(
        url,
        {"username": "taken_name"},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {access_token}",
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "USERNAME_TAKEN"


def test_finalize_username_rejects_invalid_usernames(
    client: Client, user: User, access_token: str
) -> None:
    url = reverse("authentication:finalize_username")
    response = client.post(
        url,
        {"username": "a"},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {access_token}",
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "USERNAME_LENGTH_INVALID"

    response2 = client.post(
        url,
        {"username": "_bad_start"},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {access_token}",
    )
    assert response2.status_code == 400
    assert response2.json()["error"]["code"] == "USERNAME_INVALID_FORMAT"

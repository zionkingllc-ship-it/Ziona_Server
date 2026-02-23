import os

import django
import pytest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")


@pytest.fixture(autouse=True)
def _enable_db_access(db):
    """Automatically enable database access for all tests."""
    pass


@pytest.fixture
def api_client():
    """Provide a Django test client."""
    from django.test import Client

    return Client()


@pytest.fixture
def create_user(db):
    """Factory fixture to create test users."""
    from core.users.models import User

    def _create_user(
        email="test@example.com",
        username="testuser",
        password="TestPass123!",
        is_email_verified=True,
        **kwargs,
    ):
        user = User.objects.create_user(
            email=email,
            username=username,
            password=password,
            is_email_verified=is_email_verified,
            **kwargs,
        )
        return user

    return _create_user


@pytest.fixture
def authenticated_user(create_user):
    """Create a user and generate tokens."""
    from core.authentication.tokens import TokenService

    user = create_user()
    access_token = TokenService.generate_access_token(str(user.id), user.role)
    refresh_token, jti = TokenService.generate_refresh_token(str(user.id))

    return {
        "user": user,
        "access_token": access_token,
        "refresh_token": refresh_token,
    }

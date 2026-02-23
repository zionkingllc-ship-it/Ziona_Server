import os

import pytest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")


@pytest.fixture(autouse=True)
def _enable_db_access(db):
    pass


@pytest.fixture(autouse=True)
def _set_encryption_key(settings):
    settings.ENCRYPTION_KEY = "KmL2Fsoq9wd1MR_wy_QKdKI-ghgEsnU-VBSyCrEV-Bs="


@pytest.fixture
def api_client():
    from django.test import Client

    return Client()


@pytest.fixture
def create_user(db):
    from core.users.models import User

    def _create_user(
        email="test@example.com",
        username="testuser",
        password="TestPass123!",  # noqa: S107
        is_email_verified=True,
        **kwargs,
    ):
        return User.objects.create_user(
            email=email,
            username=username,
            password=password,
            is_email_verified=is_email_verified,
            **kwargs,
        )

    return _create_user


@pytest.fixture
def authenticated_user(create_user):
    from core.authentication.tokens import TokenService

    user = create_user()
    access_token = TokenService.generate_access_token(str(user.id), user.role)
    refresh_token, jti = TokenService.generate_refresh_token(str(user.id))

    return {
        "user": user,
        "access_token": access_token,
        "refresh_token": refresh_token,
    }

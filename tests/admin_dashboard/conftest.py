import pytest

from core.authentication.tokens import TokenService


@pytest.fixture
def create_admin_user(create_user):
    def _create_admin_user(**kwargs):
        return create_user(
            email="admin@example.com",
            username="adminuser",
            role="admin",  # Use the existing 'admin' role
            **kwargs,
        )

    return _create_admin_user


@pytest.fixture
def authenticated_admin(create_admin_user):
    user = create_admin_user()
    access_token = TokenService.generate_access_token(str(user.id), user.role)
    refresh_token, jti = TokenService.generate_refresh_token(str(user.id))

    return {
        "user": user,
        "access_token": access_token,
        "refresh_token": refresh_token,
    }

from types import SimpleNamespace

import pytest
from django.test import RequestFactory

from core.authentication.permissions import IsAuthenticated
from core.authentication.tokens import TokenService


@pytest.mark.django_db
def test_graphql_permission_rejects_access_token_before_user_cutoff(create_user):
    user = create_user()
    access_token = TokenService.generate_access_token(str(user.id), user.role)
    TokenService.invalidate_user_access_tokens(str(user.id))

    request = RequestFactory().post(
        "/graphql/",
        HTTP_AUTHORIZATION=f"Bearer {access_token}",
    )
    info = SimpleNamespace(context=SimpleNamespace(request=request))

    assert IsAuthenticated().has_permission(None, info) is False


@pytest.mark.django_db
def test_graphql_permission_accepts_current_access_token(create_user):
    user = create_user()
    access_token = TokenService.generate_access_token(str(user.id), user.role)

    request = RequestFactory().post(
        "/graphql/",
        HTTP_AUTHORIZATION=f"Bearer {access_token}",
    )
    info = SimpleNamespace(context=SimpleNamespace(request=request))

    assert IsAuthenticated().has_permission(None, info) is True
    assert info.context.user.id == user.id

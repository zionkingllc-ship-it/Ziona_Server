"""The mobile-facing username grace metadata follows the actual edit policy."""

import json
from datetime import datetime, timedelta

import pytest
from django.test import Client

from core.authentication.tokens import TokenService
from core.users.services import UserService

pytestmark = pytest.mark.django_db


def _me_username_policy(user) -> dict:
    token = TokenService.generate_access_token(str(user.id), user.role)
    response = Client().post(
        "/graphql/",
        data=json.dumps(
            {"query": "{ me { usernameEditableUntil usernameGraceChangesRemaining } }"}
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    body = response.json()
    assert "errors" not in body, body.get("errors")
    return body["data"]["me"]


def test_me_exposes_grace_deadline_and_remaining_edits(create_user):
    user = create_user(username="grace_graphql")
    first = _me_username_policy(user)
    assert first["usernameGraceChangesRemaining"] == 3
    assert datetime.fromisoformat(first["usernameEditableUntil"]) == user.created_at + timedelta(
        hours=48
    )

    for name in ("grace_graphql_1", "grace_graphql_2", "grace_graphql_3"):
        UserService.set_username(str(user.id), name)
    exhausted = _me_username_policy(user)
    assert exhausted == {"usernameEditableUntil": None, "usernameGraceChangesRemaining": 0}

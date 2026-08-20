import json

import pytest
from django.test import Client, override_settings


@pytest.mark.django_db
def test_update_profile_mutation_accepts_bio_link(authenticated_user):
    client = Client()
    client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {authenticated_user['access_token']}"

    response = client.post(
        "/graphql/",
        data=json.dumps(
            {
                "query": """
                mutation UpdateProfile($bio: String, $bioLink: String) {
                  updateProfile(bio: $bio, bioLink: $bioLink) {
                    success
                    profile {
                      bio
                      bioLink
                    }
                    error {
                      code
                      message
                    }
                  }
                }
                """,
                "variables": {
                    "bio": "Building in public.",
                    "bioLink": "ziona.app/community",
                },
            }
        ),
        content_type="application/json",
    )

    content = json.loads(response.content)
    assert response.status_code == 200, content
    assert "errors" not in content, content.get("errors")

    payload = content["data"]["updateProfile"]
    assert payload["success"] is True
    assert payload["profile"]["bio"] == "Building in public."
    assert payload["profile"]["bioLink"] == "https://ziona.app/community"

    authenticated_user["user"].refresh_from_db()
    assert authenticated_user["user"].bio == "Building in public."
    assert authenticated_user["user"].bio_link == "https://ziona.app/community"


@pytest.mark.django_db
@override_settings(APP_SHARE_BASE_URL="https://share.ziona.test")
def test_user_profile_includes_share_url(authenticated_user):
    client = Client()
    client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {authenticated_user['access_token']}"
    user = authenticated_user["user"]

    response = client.post(
        "/graphql/",
        data=json.dumps(
            {
                "query": """
                query Profile($userId: String!) {
                  userProfile(userId: $userId) {
                    id
                    shareUrl
                  }
                }
                """,
                "variables": {"userId": str(user.id)},
            }
        ),
        content_type="application/json",
    )

    content = json.loads(response.content)
    assert response.status_code == 200, content
    assert "errors" not in content, content.get("errors")

    assert content["data"]["userProfile"]["shareUrl"] == (
        f"https://share.ziona.test/profile/{user.id}"
    )


@pytest.mark.django_db
@override_settings(APP_SHARE_BASE_URL="https://share.ziona.test")
def test_share_profile_external_returns_profile_link(authenticated_user, create_user):
    client = Client()
    client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {authenticated_user['access_token']}"
    target = create_user(email="shared-profile@test.com", username="shared_profile")

    response = client.post(
        "/graphql/",
        data=json.dumps(
            {
                "query": """
                mutation ShareProfile($userId: String!) {
                  shareProfileExternal(userId: $userId) {
                    success
                    shareId
                    shareType
                    shareUrl
                    errorCode
                  }
                }
                """,
                "variables": {"userId": str(target.id)},
            }
        ),
        content_type="application/json",
    )

    content = json.loads(response.content)
    assert response.status_code == 200, content
    assert "errors" not in content, content.get("errors")

    payload = content["data"]["shareProfileExternal"]
    assert payload["success"] is True
    assert payload["shareId"] is None
    assert payload["shareType"] == "profile_external"
    assert payload["shareUrl"] == f"https://share.ziona.test/profile/{target.id}"

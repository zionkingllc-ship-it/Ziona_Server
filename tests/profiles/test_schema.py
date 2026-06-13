import json

import pytest
from django.test import Client


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

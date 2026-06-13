import json

import pytest
from django.test import Client


@pytest.mark.django_db
def test_suggested_creators_query_returns_profile_stats_without_runtime_error(
    authenticated_user, create_user
):
    create_user(email="creator@example.com", username="creator_one")

    client = Client()
    client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {authenticated_user['access_token']}"

    response = client.post(
        "/graphql/",
        data=json.dumps(
            {
                "query": """
                query SuggestedCreators {
                  suggestedCreators {
                    id
                    username
                    stats {
                      followersCount
                      followingCount
                      postsCount
                    }
                  }
                }
                """
            }
        ),
        content_type="application/json",
    )

    content = json.loads(response.content)
    assert response.status_code == 200, content
    assert "errors" not in content, content.get("errors")

    suggestions = content["data"]["suggestedCreators"]
    assert len(suggestions) == 1
    assert suggestions[0]["username"] == "creator_one"
    assert suggestions[0]["stats"]["followersCount"] == "0"

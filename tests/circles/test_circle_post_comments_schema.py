import json

import pytest
from django.test import Client

from core.circles.models import Circle, CirclePost


@pytest.mark.django_db
def test_circle_post_comments_query_is_registered(authenticated_user):
    user = authenticated_user["user"]
    circle = Circle.objects.create(
        name="Comment Schema Circle",
        description="Circle for comment schema test",
        cover_image="https://example.com/cover.jpg",
    )
    post = CirclePost.objects.create(
        circle=circle,
        user=user,
        text="Post for comment query",
    )
    client = Client()
    client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {authenticated_user['access_token']}"

    response = client.post(
        "/graphql/",
        data=json.dumps(
            {
                "query": """
                query CirclePostComments($postId: String!) {
                  circlePostComments(postId: $postId, page: 1, pageSize: 10) {
                    comments {
                      id
                      text
                      likesCount
                      author { name avatar }
                      viewerState { liked }
                    }
                    pageInfo { hasNextPage totalCount currentPage }
                  }
                }
                """,
                "variables": {"postId": str(post.id)},
            }
        ),
        content_type="application/json",
    )

    content = json.loads(response.content)
    assert "errors" not in content
    assert content["data"]["circlePostComments"]["comments"] == []
    assert content["data"]["circlePostComments"]["pageInfo"]["totalCount"] == 0

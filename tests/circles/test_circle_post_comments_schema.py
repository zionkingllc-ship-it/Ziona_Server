import json

import pytest
from django.test import Client

from core.circles.models import Circle, CircleMembership, CirclePost, CirclePostComment


@pytest.mark.django_db
def test_circle_post_comments_query_is_registered(authenticated_user):
    user = authenticated_user["user"]
    user.username = "comment_author"
    user.full_name = "Comment Author"
    user.save(update_fields=["username", "full_name"])
    circle = Circle.objects.create(
        name="Comment Schema Circle",
        description="Circle for comment schema test",
        cover_image="https://example.com/cover.jpg",
    )
    CircleMembership.objects.create(circle=circle, user=user, role="admin")
    post = CirclePost.objects.create(
        circle=circle,
        user=user,
        text="Post for comment query",
    )
    comment = CirclePostComment.objects.create(
        post=post,
        user=user,
        text="First threaded comment",
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
                      author { name username avatar }
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
    comments = content["data"]["circlePostComments"]["comments"]
    assert comments[0]["id"] == str(comment.id)
    assert comments[0]["author"]["name"] == "Comment Author"
    assert comments[0]["author"]["username"] == "comment_author"
    assert content["data"]["circlePostComments"]["pageInfo"]["totalCount"] == 1

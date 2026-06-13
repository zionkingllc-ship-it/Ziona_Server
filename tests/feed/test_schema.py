import json

import pytest
from django.test import Client

from core.posts.models import Post


@pytest.mark.django_db
def test_following_feed_graphql_returns_followed_authors_only(authenticated_user, create_user):
    from core.follows.services import FollowService

    viewer = authenticated_user["user"]
    followed_author = create_user(email="graphql-followed@test.com", username="graphql_followed")
    unfollowed_author = create_user(
        email="graphql-unfollowed@test.com",
        username="graphql_unfollowed",
    )

    FollowService.follow_user(str(viewer.id), str(followed_author.id))

    followed_post = Post.objects.create(
        user=followed_author,
        post_type="text",
        caption="Followed post",
    )
    Post.objects.create(
        user=unfollowed_author,
        post_type="text",
        caption="Unfollowed post",
    )

    client = Client()
    client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {authenticated_user['access_token']}"

    response = client.post(
        "/graphql/",
        data=json.dumps(
            {
                "query": """
                query FollowingFeed {
                  followingFeed(limit: 10) {
                    posts {
                      id
                      author {
                        id
                        username
                      }
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

    posts = content["data"]["followingFeed"]["posts"]
    returned_post_ids = {post["id"] for post in posts}
    returned_author_ids = {post["author"]["id"] for post in posts}

    assert str(followed_post.id) in returned_post_ids
    assert returned_author_ids == {str(followed_author.id)}

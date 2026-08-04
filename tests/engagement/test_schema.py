import json

import pytest
from django.test import Client

from core.posts.models import Post


@pytest.mark.django_db
class TestEngagementSchema:
    @pytest.fixture
    def auth_client(self, authenticated_user):
        client = Client()
        client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {authenticated_user['access_token']}"
        return client

    @pytest.fixture
    def user(self, authenticated_user):
        return authenticated_user["user"]

    def test_create_comment_returns_canonical_post_stats(self, auth_client, user):
        post = Post.objects.create(
            user=user,
            post_type="text",
            caption="Comment stats post",
        )

        response = auth_client.post(
            "/graphql/",
            data=json.dumps(
                {
                    "query": """
                    mutation CreateComment($postId: String!, $text: String!) {
                      createComment(postId: $postId, text: $text) {
                        success
                        comment { id text }
                        stats {
                          likesCount
                          commentsCount
                          sharesCount
                          savesCount
                        }
                        error { code message }
                      }
                    }
                    """,
                    "variables": {"postId": str(post.id), "text": "First comment"},
                }
            ),
            content_type="application/json",
        )

        content = json.loads(response.content)
        assert "errors" not in content

        payload = content["data"]["createComment"]
        assert payload["success"] is True
        assert payload["comment"]["text"] == "First comment"
        assert payload["stats"]["commentsCount"] == 1
        assert payload["stats"]["likesCount"] == 0

    def test_delete_comment_returns_canonical_post_stats(self, auth_client, user):
        post = Post.objects.create(
            user=user,
            post_type="text",
            caption="Delete comment stats post",
        )

        create_response = auth_client.post(
            "/graphql/",
            data=json.dumps(
                {
                    "query": """
                    mutation CreateComment($postId: String!, $text: String!) {
                      createComment(postId: $postId, text: $text) {
                        comment { id }
                      }
                    }
                    """,
                    "variables": {"postId": str(post.id), "text": "Delete me"},
                }
            ),
            content_type="application/json",
        )
        create_content = json.loads(create_response.content)
        assert "errors" not in create_content
        comment_id = create_content["data"]["createComment"]["comment"]["id"]

        delete_response = auth_client.post(
            "/graphql/",
            data=json.dumps(
                {
                    "query": """
                    mutation DeleteComment($commentId: String!) {
                      deleteComment(commentId: $commentId) {
                        success
                        stats {
                          likesCount
                          commentsCount
                          sharesCount
                          savesCount
                        }
                        error { code message }
                      }
                    }
                    """,
                    "variables": {"commentId": comment_id},
                }
            ),
            content_type="application/json",
        )
        delete_content = json.loads(delete_response.content)
        assert "errors" not in delete_content

        payload = delete_content["data"]["deleteComment"]
        assert payload["success"] is True
        assert payload["stats"]["commentsCount"] == 0
        assert payload["stats"]["likesCount"] == 0

    def test_comment_like_can_be_undone_through_the_api(self, auth_client, user):
        """P1 regression: unlikeComment must exist and be reachable.

        Users could like a comment but had no way to remove it — likeComment is
        add-only and no unlikeComment mutation was exposed.
        """
        post = Post.objects.create(user=user, post_type="text", caption="Unlike target")
        create = auth_client.post(
            "/graphql/",
            data=json.dumps(
                {
                    "query": """
                    mutation ($postId: String!, $text: String!) {
                      createComment(postId: $postId, text: $text) {
                        success comment { id }
                      }
                    }
                    """,
                    "variables": {"postId": str(post.id), "text": "A comment"},
                }
            ),
            content_type="application/json",
        )
        comment_id = json.loads(create.content)["data"]["createComment"]["comment"]["id"]

        def run(mutation_name):
            response = auth_client.post(
                "/graphql/",
                data=json.dumps(
                    {
                        "query": f"""
                        mutation ($commentId: String!) {{
                          {mutation_name}(commentId: $commentId) {{
                            success
                            liked
                            commentStats {{ likesCount repliesCount }}
                            errorCode
                          }}
                        }}
                        """,
                        "variables": {"commentId": comment_id},
                    }
                ),
                content_type="application/json",
            )
            body = json.loads(response.content)
            assert "errors" not in body, body.get("errors")
            return body["data"][mutation_name]

        liked = run("likeComment")
        assert liked["success"] is True
        assert liked["liked"] is True
        assert liked["commentStats"]["likesCount"] == 1

        unliked = run("unlikeComment")
        assert unliked["success"] is True
        assert unliked["liked"] is False
        assert unliked["commentStats"]["likesCount"] == 0

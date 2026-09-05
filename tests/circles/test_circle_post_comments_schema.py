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


@pytest.mark.django_db
def test_reported_comment_disappears_for_the_reporter_end_to_end(authenticated_user, create_user):
    """The whole loop through the real resolver: report → gone, count included."""
    from core.circles.moderation_services import report_circle_content

    reporter = authenticated_user["user"]
    author = create_user(email="cmt-author@example.com", username="cmt_author")
    circle = Circle.objects.create(
        name="Reported Comment Circle",
        description="Circle for the comment report test",
        cover_image="https://example.com/cover.jpg",
    )
    CircleMembership.objects.create(circle=circle, user=reporter, role="member")
    CircleMembership.objects.create(circle=circle, user=author, role="member")
    post = CirclePost.objects.create(circle=circle, user=author, text="Post with a bad comment")
    CirclePostComment.objects.create(post=post, user=author, text="Objectionable comment")

    client = Client()
    client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {authenticated_user['access_token']}"
    query = """
        query CirclePostComments($postId: String!) {
          circlePostComments(postId: $postId, page: 1, pageSize: 10) {
            comments { id text }
            pageInfo { totalCount }
          }
        }
    """

    def _fetch():
        response = client.post(
            "/graphql/",
            data=json.dumps({"query": query, "variables": {"postId": str(post.id)}}),
            content_type="application/json",
        )
        body = json.loads(response.content)
        assert "errors" not in body, body.get("errors")
        return body["data"]["circlePostComments"]

    before = _fetch()
    assert len(before["comments"]) == 1
    assert before["pageInfo"]["totalCount"] == 1

    comment_id = before["comments"][0]["id"]
    report_circle_content(reporter.id, "comment", comment_id, "Spam", circle.id)

    after = _fetch()
    assert after["comments"] == []
    assert after["pageInfo"]["totalCount"] == 0

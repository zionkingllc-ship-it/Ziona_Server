"""Malformed comment ids must return a clean domain error.

Comment ids are UUID primary keys, so a non-UUID string made Django raise
ValidationError when the query compiled. That escaped the resolvers' EngagementError
handlers and reached the client as a bare GraphQL error carrying a Django-internal
message ("comment_123 is not a valid UUID") with no errorCode — and createComment
mislabelled it INTERNAL_ERROR.

These ids are client-fabricated (optimistic-UI placeholders); the backend only ever
returns canonical UUIDs. The backend cannot accept them — it can only fail cleanly.
"""

import json
import uuid

import pytest
from django.test import Client

from core.engagement.services import EngagementService
from core.posts.models import Post
from core.shared.exceptions import EngagementError

MALFORMED_IDS = ["comment_123", "cmt-uuid-format", "not-a-uuid", ""]


@pytest.fixture
def user_a(create_user):
    return create_user(email="a@test.com", username="user_a")


@pytest.fixture
def post(user_a):
    return Post.objects.create(user=user_a, post_type="text", caption="Test post")


@pytest.mark.django_db
@pytest.mark.parametrize("bad_id", MALFORMED_IDS)
def test_like_comment_rejects_malformed_id_cleanly(user_a, bad_id):
    with pytest.raises(EngagementError) as exc:
        EngagementService.like_comment(str(user_a.id), bad_id)
    assert exc.value.code == "COMMENT_NOT_FOUND"


@pytest.mark.django_db
@pytest.mark.parametrize("bad_id", MALFORMED_IDS)
def test_unlike_comment_rejects_malformed_id_cleanly(user_a, bad_id):
    with pytest.raises(EngagementError) as exc:
        EngagementService.unlike_comment(str(user_a.id), bad_id)
    assert exc.value.code == "COMMENT_NOT_FOUND"


@pytest.mark.django_db
@pytest.mark.parametrize("bad_id", MALFORMED_IDS)
def test_delete_comment_rejects_malformed_id_cleanly(user_a, bad_id):
    with pytest.raises(EngagementError) as exc:
        EngagementService.delete_comment(str(user_a.id), bad_id)
    assert exc.value.code == "COMMENT_NOT_FOUND"


@pytest.mark.django_db
def test_create_reply_with_malformed_parent_id_is_not_internal_error(user_a, post):
    """Previously degraded into a misleading INTERNAL_ERROR."""
    with pytest.raises(EngagementError) as exc:
        EngagementService.create_comment(
            user_id=str(user_a.id),
            post_id=str(post.id),
            text="a reply",
            parent_comment_id="comment_123",
        )
    assert exc.value.code == "COMMENT_NOT_FOUND"


@pytest.mark.django_db
def test_comment_replies_query_returns_empty_for_malformed_id(user_a):
    """Query path has no error payload, so it mirrors the missing-id behaviour."""
    result = EngagementService.get_comment_replies("comment_123", viewer_id=str(user_a.id))

    assert result.comments == []
    assert result.total_count == 0
    assert result.has_more is False


@pytest.mark.django_db
def test_backend_only_ever_returns_uuid_comment_ids(user_a, post):
    """Proves the client can never legitimately receive an id like 'comment_123'."""
    comment = EngagementService.create_comment(
        user_id=str(user_a.id), post_id=str(post.id), text="hello"
    )

    uuid.UUID(comment.id)  # raises if not canonical
    assert comment.id == str(uuid.UUID(comment.id))


@pytest.mark.django_db
def test_malformed_id_returns_structured_payload_not_graphql_error(authenticated_user):
    """End-to-end: no leaked Django message, and an errorCode the client can read."""
    client = Client()
    client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {authenticated_user['access_token']}"

    response = client.post(
        "/graphql/",
        data=json.dumps(
            {
                "query": """
                mutation ($commentId: String!) {
                  likeComment(commentId: $commentId) {
                    success
                    errorCode
                    message
                  }
                }
                """,
                "variables": {"commentId": "comment_123"},
            }
        ),
        content_type="application/json",
    )

    body = json.loads(response.content)
    # The whole point: a structured payload, not a top-level GraphQL error.
    assert "errors" not in body, body.get("errors")
    payload = body["data"]["likeComment"]
    assert payload["success"] is False
    assert payload["errorCode"] == "COMMENT_NOT_FOUND"
    assert "is not a valid UUID" not in (payload["message"] or "")

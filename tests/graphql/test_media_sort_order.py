"""`sortOrder` must be exposed on media through the GraphQL API.

The creator's selected image order was already stored (through-table `position`),
annotated onto the queryset, and carried into `MediaItemDTO.order` — but the
GraphQL mappers dropped it, so `MediaFileType` had no such field. Clients could
only rely on list order, with no value to re-sort by after caching or merging.

These assert the field exists and is correct on all three surfaces that share
`MediaFileType`: a single post, the feed, and a circle post.
"""

import json

import pytest
from django.test import Client

from core.media.models import MediaFile
from core.posts.services import PostService


def _ready_image(user, name):
    return MediaFile.objects.create(
        user=user,
        file_name=name,
        storage_path=name,
        media_type="image",
        file_size=1024,
        status="ready",
    )


def _auth_client(authenticated_user):
    client = Client()
    client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {authenticated_user['access_token']}"
    return client


def _gql(client, query, variables=None):
    response = client.post(
        "/graphql/",
        data=json.dumps({"query": query, "variables": variables or {}}),
        content_type="application/json",
    )
    body = json.loads(response.content)
    assert "errors" not in body, body.get("errors")
    return body["data"]


@pytest.fixture
def ordered_post(authenticated_user):
    """A 3-image post whose selected order differs from upload order."""
    user = authenticated_user["user"]
    a = _ready_image(user, "a.jpg")
    b = _ready_image(user, "b.jpg")
    c = _ready_image(user, "c.jpg")
    selected = [str(c.id), str(a.id), str(b.id)]
    post = PostService.create_post(
        user_id=str(user.id),
        post_type="image",
        caption="ordered images",
        media_ids=selected,
    )
    return {"post": post, "selected": selected}


@pytest.mark.django_db
def test_post_query_exposes_sort_order(authenticated_user, ordered_post):
    client = _auth_client(authenticated_user)

    data = _gql(
        client,
        """
        query ($id: ID!) {
          post(id: $id) {
            media { id sortOrder }
          }
        }
        """,
        {"id": str(ordered_post["post"].id)},
    )

    media = data["post"]["media"]
    assert [m["sortOrder"] for m in media] == [0, 1, 2]
    # sortOrder tracks the creator's selection, not upload time.
    assert [m["id"] for m in media] == ordered_post["selected"]


@pytest.mark.django_db
def test_feed_exposes_sort_order(authenticated_user, ordered_post):
    client = _auth_client(authenticated_user)

    data = _gql(
        client,
        """
        query {
          forYouFeed(limit: 10) {
            posts { image { items { id sortOrder } } }
          }
        }
        """,
    )

    posts = data["forYouFeed"]["posts"]
    with_media = [p for p in posts if p["image"] and p["image"]["items"]]
    assert with_media, "expected the ordered post in the feed"

    items = with_media[0]["image"]["items"]
    assert [i["sortOrder"] for i in items] == [0, 1, 2]
    assert [i["id"] for i in items] == ordered_post["selected"]


@pytest.mark.django_db
def test_sort_order_is_zero_for_single_media_post(authenticated_user):
    client = _auth_client(authenticated_user)
    user = authenticated_user["user"]
    only = _ready_image(user, "solo.jpg")
    post = PostService.create_post(
        user_id=str(user.id),
        post_type="image",
        caption="single",
        media_ids=[str(only.id)],
    )

    data = _gql(
        client,
        "query ($id: ID!) { post(id: $id) { media { sortOrder } } }",
        {"id": str(post.id)},
    )

    assert [m["sortOrder"] for m in data["post"]["media"]] == [0]


@pytest.mark.django_db
def test_circle_post_media_exposes_sort_order(authenticated_user):
    """Circle media is mapped from the ORM object, not a DTO — separate path."""
    from core.circles.models import Circle, CircleMembership
    from core.circles.services import create_circle_post

    user = authenticated_user["user"]
    circle = Circle.objects.create(name="Sort Order Circle", description="x")
    CircleMembership.objects.create(circle=circle, user=user, role="member")

    a = _ready_image(user, "c-a.jpg")
    b = _ready_image(user, "c-b.jpg")
    c = _ready_image(user, "c-c.jpg")
    selected = [str(c.id), str(a.id), str(b.id)]

    post = create_circle_post(
        user_id=str(user.id),
        circle_id=str(circle.id),
        text="ordered circle images",
        media_ids=selected,
    )

    client = _auth_client(authenticated_user)
    data = _gql(
        client,
        """
        query ($id: String!) {
          circlePost(id: $id) {
            media { id sortOrder }
          }
        }
        """,
        {"id": str(post.id)},
    )

    media = data["circlePost"]["media"]
    assert [m["sortOrder"] for m in media] == [0, 1, 2]
    assert [m["id"] for m in media] == selected

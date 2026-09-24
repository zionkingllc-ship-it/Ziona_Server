import json

import pytest
from django.test import Client
from django.utils import timezone

from core.posts.models import Post


def make_category(category_id, label, slug, order=1):
    from core.categories.models import Category

    return Category.objects.create(
        id=category_id,
        label=label,
        slug=slug,
        icon=f"https://example.com/{slug}.png",
        bg_color="#ffffff",
        bd_color="#111111",
        order=order,
    )


def attach_ready_media(post, user, media_type):
    from core.media.models import MediaFile, MediaStatus
    from core.posts.models import PostMediaThrough

    media = MediaFile.objects.create(
        user=user,
        file_name=f"{media_type}.{'mp4' if media_type == 'video' else 'jpg'}",
        file_type="video/mp4" if media_type == "video" else "image/jpeg",
        file_size=1024,
        media_type=media_type,
        storage_path=f"tests/{post.id}/{media_type}",
        thumbnail_path=f"tests/{post.id}/thumb.jpg" if media_type == "video" else "",
        status=MediaStatus.READY,
        width=720,
        height=1280,
        duration=12 if media_type == "video" else None,
    )
    PostMediaThrough.objects.create(post=post, mediafile=media, position=0)


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


@pytest.mark.django_db
def test_discover_feed_graphql_filters_category_label_and_media_type(create_user):
    author = create_user(email="graphql-discover@test.com", username="graphql_discover")
    love = make_category("graphql-love", "Love", "graphql-love", order=1)
    trust = make_category("graphql-trust", "Trust", "graphql-trust", order=2)

    love_video = Post.objects.create(
        user=author,
        post_type="video",
        caption="Love video",
        category=love,
        media_count=1,
    )
    attach_ready_media(love_video, author, "video")
    love_text = Post.objects.create(
        user=author,
        post_type="text",
        caption="Love text",
        category=love,
    )
    Post.objects.create(
        user=author,
        post_type="video",
        caption="Trust video",
        category=trust,
        media_count=1,
    )

    response = Client().post(
        "/graphql/",
        data=json.dumps(
            {
                "query": """
                query Discover($category: String, $mediaType: String) {
                  discoverFeed(category: $category, mediaType: $mediaType, limit: 10) {
                    posts {
                      id
                      mediaType
                      category { label }
                    }
                  }
                }
                """,
                "variables": {"category": "Love", "mediaType": "VIDEO"},
            }
        ),
        content_type="application/json",
    )

    content = json.loads(response.content)
    assert response.status_code == 200, content
    assert "errors" not in content, content.get("errors")

    posts = content["data"]["discoverFeed"]["posts"]
    assert [post["id"] for post in posts] == [str(love_video.id)]
    assert posts[0]["mediaType"] == "video"
    assert posts[0]["category"]["label"] == "Love"
    assert str(love_text.id) not in {post["id"] for post in posts}


@pytest.mark.django_db
def test_discover_search_graphql_returns_creators_and_posts(create_user):
    creator = create_user(email="creator-search@test.com", username="grace_creator")
    author = create_user(email="author-search@test.com", username="author_search")
    love = make_category("search-love", "Love", "search-love", order=1)
    post = Post.objects.create(
        user=author,
        post_type="text",
        caption="Grace testimony from today",
        category=love,
    )

    response = Client().post(
        "/graphql/",
        data=json.dumps(
            {
                "query": """
                query Search($query: String!) {
                  discoverSearch(query: $query, limit: 10) {
                    creatorCount
                    postCount
                    creators { id username }
                    posts { id textMessage category { label } }
                    emptyState { message }
                  }
                }
                """,
                "variables": {"query": "grace"},
            }
        ),
        content_type="application/json",
    )

    content = json.loads(response.content)
    assert response.status_code == 200, content
    assert "errors" not in content, content.get("errors")

    payload = content["data"]["discoverSearch"]
    assert str(creator.id) in {item["id"] for item in payload["creators"]}
    assert [item["id"] for item in payload["posts"]] == [str(post.id)]
    assert payload["posts"][0]["category"]["label"] == "Love"
    assert payload["emptyState"] is None


@pytest.mark.django_db
def test_discover_search_graphql_respects_category_slug_and_media_alias(create_user):
    author = create_user(email="media-search@test.com", username="media_search")
    love = make_category("search-love-media", "Love", "search-love-media", order=1)
    trust = make_category("search-trust-media", "Trust", "search-trust-media", order=2)
    image_post = Post.objects.create(
        user=author,
        post_type="image",
        caption="Grace image",
        category=love,
        media_count=1,
    )
    attach_ready_media(image_post, author, "image")
    Post.objects.create(
        user=author,
        post_type="text",
        caption="Grace text",
        category=love,
    )
    Post.objects.create(
        user=author,
        post_type="image",
        caption="Grace image outside category",
        category=trust,
        media_count=1,
    )

    response = Client().post(
        "/graphql/",
        data=json.dumps(
            {
                "query": """
                query Search($query: String!, $category: String, $mediaType: String) {
                  discoverSearch(
                    query: $query
                    category: $category
                    mediaType: $mediaType
                    limit: 10
                  ) {
                    posts { id mediaType category { slug } }
                  }
                }
                """,
                "variables": {
                    "query": "Grace",
                    "category": "search-love-media",
                    "mediaType": "Images",
                },
            }
        ),
        content_type="application/json",
    )

    content = json.loads(response.content)
    assert response.status_code == 200, content
    assert "errors" not in content, content.get("errors")

    posts = content["data"]["discoverSearch"]["posts"]
    assert [item["id"] for item in posts] == [str(image_post.id)]
    assert posts[0]["mediaType"] == "image"


@pytest.mark.django_db
def test_discover_search_graphql_returns_empty_state_for_blank_query():
    response = Client().post(
        "/graphql/",
        data=json.dumps(
            {
                "query": """
                query Search($query: String!) {
                  discoverSearch(query: $query, limit: 10) {
                    creators { id }
                    posts { id }
                    emptyState { message }
                  }
                }
                """,
                "variables": {"query": ""},
            }
        ),
        content_type="application/json",
    )

    content = json.loads(response.content)
    assert response.status_code == 200, content
    assert "errors" not in content, content.get("errors")

    payload = content["data"]["discoverSearch"]
    assert payload["creators"] == []
    assert payload["posts"] == []
    assert payload["emptyState"]["message"] == "No matching content found."


@pytest.mark.django_db
def test_discover_search_returns_creators_only_on_the_first_page(create_user):
    """The cursor paginates posts; creators are a header block above them.

    The client accumulates pages with `pages.flatMap(p => p.creators)`, so
    re-sending page 1 of creators alongside every page of posts made the same
    handful of names repeat down the list — once more on each scroll.
    """
    creator = create_user(email="dup-creator@test.com", username="grace_creator")
    author = create_user(email="dup-author@test.com", username="grace_author")
    love = make_category("dup-love", "Love", "dup-love", order=1)
    for index in range(6):
        Post.objects.create(
            user=author,
            post_type="text",
            caption=f"Grace testimony {index}",
            category=love,
        )

    def search(cursor=None):
        variables = {"query": "grace", "cursor": cursor}
        response = Client().post(
            "/graphql/",
            data=json.dumps(
                {
                    "query": """
                    query Search($query: String!, $cursor: String) {
                      discoverSearch(query: $query, limit: 2, cursor: $cursor) {
                        creators { id username }
                        posts { id }
                        nextCursor
                        hasMore
                        emptyState { message }
                      }
                    }
                    """,
                    "variables": variables,
                }
            ),
            content_type="application/json",
        )
        content = json.loads(response.content)
        assert "errors" not in content, content.get("errors")
        return content["data"]["discoverSearch"]

    first = search()
    assert str(creator.id) in {item["id"] for item in first["creators"]}
    assert first["hasMore"] is True

    # Walk the rest of the feed the way the client does and accumulate.
    accumulated = [item["id"] for item in first["creators"]]
    cursor = first["nextCursor"]
    pages = 0
    while cursor and pages < 5:
        page = search(cursor)
        assert page["creators"] == [], "creators must not repeat on a continuation page"
        # A continuation page is mid-scroll — never an empty state.
        assert page["emptyState"] is None
        accumulated.extend(item["id"] for item in page["creators"])
        cursor = page["nextCursor"]
        pages += 1

    assert pages > 0, "fixture must produce more than one page for this to prove anything"
    assert len(accumulated) == len(set(accumulated))


@pytest.mark.django_db
def test_discover_search_continuation_page_never_reports_an_empty_state(create_user):
    """ "No matching content" belongs to a search that found nothing, not to
    running off the end of one that did.

    Reachable whenever the remaining posts disappear between pages — deleted,
    hidden, or reported — which leaves a continuation page with nothing in it.
    """
    create_user(email="empty-creator@test.com", username="grace_creator")
    author = create_user(email="empty-author@test.com", username="grace_author")
    love = make_category("empty-love", "Love", "empty-love", order=1)
    posts = [
        Post.objects.create(
            user=author, post_type="text", caption=f"Grace testimony {index}", category=love
        )
        for index in range(6)
    ]

    def search(cursor=None):
        response = Client().post(
            "/graphql/",
            data=json.dumps(
                {
                    "query": """
                    query Search($query: String!, $cursor: String) {
                      discoverSearch(query: $query, limit: 2, cursor: $cursor) {
                        creators { id }
                        posts { id }
                        nextCursor
                        emptyState { message }
                      }
                    }
                    """,
                    "variables": {"query": "grace", "cursor": cursor},
                }
            ),
            content_type="application/json",
        )
        content = json.loads(response.content)
        assert "errors" not in content, content.get("errors")
        return content["data"]["discoverSearch"]

    first = search()
    cursor = first["nextCursor"]
    assert cursor, "fixture must produce a second page"

    # Everything past page one vanishes while the user is still scrolling.
    Post.objects.filter(id__in=[p.id for p in posts]).update(deleted_at=timezone.now())

    page_two = search(cursor)

    assert page_two["posts"] == []
    assert page_two["emptyState"] is None, "mid-scroll exhaustion is not an empty search"

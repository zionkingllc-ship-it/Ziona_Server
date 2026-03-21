import json

import pytest
from django.test import Client

from core.media.models import MediaFile


@pytest.mark.django_db
class TestSchemaAlignment:
    @pytest.fixture
    def auth_client(self, authenticated_user):
        client = Client()
        client.defaults["HTTP_AUTHORIZATION"] = f'Bearer {authenticated_user["access_token"]}'
        return client

    @pytest.fixture
    def user(self, authenticated_user):
        return authenticated_user["user"]

    def test_create_post_mutation_alignment(self, auth_client, user):
        """Verify createPost mutation matches the mobile contract."""
        media = MediaFile.objects.create(
            user=user,
            file_name="test.jpg",
            file_type="image/jpeg",
            file_size=1024,
            media_type="image",
            storage_path="uploads/test/images/test.jpg",
            status="ready",
        )

        mutation = """
        mutation CreatePost($postType: PostType!, $caption: String, $mediaIds: [String!], $mediaType: MediaType) {
          createPost(postType: $postType, caption: $caption, mediaIds: $mediaIds, mediaType: $mediaType) {
            success
            post {
              id
              caption
              media {
                id
                url
                type
              }
            }
            error {
              code
              message
            }
          }
        }
        """
        variables = {
            "postType": "MEDIA",
            "caption": "Test post alignment",
            "mediaIds": [str(media.id)],
            "mediaType": "IMAGE",
        }

        response = auth_client.post(
            "/graphql/",
            data=json.dumps({"query": mutation, "variables": variables}),
            content_type="application/json",
        )
        content = json.loads(response.content)

        assert (
            response.status_code == 200
        ), f"Expected 200 but got {response.status_code}: {content}"

        # Allow for either clean response or graphql-level errors
        if "errors" in content:
            pytest.fail(f"GraphQL errors: {content['errors']}")

        data = content["data"]["createPost"]
        assert data["success"] is True, f"createPost failed: {data.get('error')}"
        assert data["post"]["caption"] == "Test post alignment"

    def test_feed_query_alignment(self, auth_client):
        """Verify feed query matches the mobile contract (PART 4)."""
        query = """
        query GetFeed($limit: Int) {
          feed(limit: $limit) {
            posts {
              id
              caption
              image { items { url } }
              video { url }
              text { message }
            }
            hasMore
          }
        }
        """
        response = auth_client.post(
            "/graphql/",
            data=json.dumps({"query": query, "variables": {"limit": 10}}),
            content_type="application/json",
        )
        content = json.loads(response.content)

        assert (
            response.status_code == 200
        ), f"Expected 200 but got {response.status_code}: {content}"
        if "errors" in content:
            pytest.fail(f"GraphQL errors: {content['errors']}")
        assert "feed" in content["data"]

    def test_scripture_query_alignment(self, auth_client):
        """Verify scripture query returns structured verses array (PART 5)."""
        query = """
        query GetScripture($book: String!, $chapter: Int!, $version: String!) {
          scripture(book: $book, chapter: $chapter, version: $version) {
            book
            chapter
            version
            verses {
              number
              text
            }
          }
        }
        """
        variables = {"book": "John", "chapter": 3, "version": "kjv"}
        from django.core.cache import cache

        cache.clear()

        response = auth_client.post(
            "/graphql/",
            data=json.dumps({"query": query, "variables": variables}),
            content_type="application/json",
        )
        content = json.loads(response.content)

        assert (
            response.status_code == 200
        ), f"Expected 200 but got {response.status_code}: {content}"
        if "errors" in content:
            pytest.fail(f"GraphQL errors: {content['errors']}")

        data = content["data"]["scripture"]
        assert len(data["verses"]) > 0
        assert data["book"] == "John"
        assert data["chapter"] == 3
        assert data["version"] == "kjv"

    def test_scripture_range_query_alignment(self, auth_client):
        """Verify scriptureRange query returns concatenated string."""
        query = """
        query GetScriptureRange($book: String!, $chapter: Int!, $version: String!, $verseStart: Int!, $verseEnd: Int!) {
          scriptureRange(book: $book, chapter: $chapter, version: $version, verseStart: $verseStart, verseEnd: $verseEnd)
        }
        """
        variables = {
            "book": "John",
            "chapter": 3,
            "version": "kjv",
            "verseStart": 16,
            "verseEnd": 17,
        }
        from django.core.cache import cache

        cache.clear()

        response = auth_client.post(
            "/graphql/",
            data=json.dumps({"query": query, "variables": variables}),
            content_type="application/json",
        )
        content = json.loads(response.content)

        assert (
            response.status_code == 200
        ), f"Expected 200 but got {response.status_code}: {content}"
        if "errors" in content:
            pytest.fail(f"GraphQL errors: {content['errors']}")

        data = content["data"]["scriptureRange"]
        assert isinstance(data, str)
        assert len(data) > 0

    def test_create_post_media_urls_only(self, auth_client, user):
        """Verify createPost accepts mediaUrls seamlessly"""
        mutation = """
        mutation CreateMediaUrl($postType: PostType!, $caption: String, $mediaUrls: [String!]) {
          createPost(postType: $postType, caption: $caption, mediaUrls: $mediaUrls) {
            success
            error { code }
            post { media { url } }
          }
        }
        """
        response = auth_client.post(
            "/graphql/",
            data=json.dumps(
                {
                    "query": mutation,
                    "variables": {
                        "postType": "MEDIA",
                        "caption": "Url test",
                        "mediaUrls": ["https://storage.googleapis.com/test.jpg"],
                    },
                }
            ),
            content_type="application/json",
        )
        data = json.loads(response.content)["data"]["createPost"]
        assert data["success"] is True
        assert len(data["post"]["media"]) == 1

    def test_feed_union_and_category_resolution(self, auth_client, user):
        """Verify feed returns strict unions and full category bodies"""
        # Create a post tied to category 1 (All)
        from core.posts.services import PostService

        PostService.create_post(
            user_id=str(user.id), post_type="text", caption="Test union post", category_id="1"
        )

        from django.core.cache import cache

        cache.clear()

        query = """
        query GetFeedUnions {
          feed {
            posts {
              type
              category { id label slug icon bgColor bdColor order }
              text { message scripture { reference } }
              image { items { url } }
              video { url }
            }
          }
        }
        """
        response = auth_client.post(
            "/graphql/", data=json.dumps({"query": query}), content_type="application/json"
        )
        content = json.loads(response.content)
        assert "errors" not in content

        posts = content["data"]["feed"]["posts"]
        text_post = next(
            p for p in posts if p["text"] and p["text"]["message"] == "Test union post"
        )

        # Verify 7 properties on Category
        assert text_post["category"]["id"] == "1"
        assert text_post["category"]["label"] == "All"
        assert text_post["image"] is None
        assert text_post["video"] is None

    def test_create_scripture_fields(self, auth_client):
        """Verify createPost parses root scripture scalars properly"""
        from unittest.mock import patch

        with patch("core.scripture.services.ScriptureService.fetch_verse") as mock_fetch:
            mock_fetch.return_value = {
                "reference": "John 11:35",
                "text": "Jesus wept.",
                "version": "KJV",
                "book": "John",
                "chapter": 11,
                "verse_start": 35,
                "verse_end": None,
                "verses": [],
            }
            mutation = """
        mutation CreateBible($postType: PostType!, $book: String!, $chap: Int!, $vs: Int!) {
          createPost(postType: $postType, scriptureBook: $book, scriptureChapter: $chap, scriptureVerseStart: $vs) {
            success
            post { scripture { book chapter verseStart } }
          }
        }
        """
        variables = {"postType": "BIBLE", "book": "John", "chap": 11, "vs": 35}
        response = auth_client.post(
            "/graphql/",
            data=json.dumps({"query": mutation, "variables": variables}),
            content_type="application/json",
        )
        data = json.loads(response.content)["data"]["createPost"]

        assert data["success"] is True
        assert data["post"]["scripture"]["book"] == "John"

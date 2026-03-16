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
              media {
                url
                type
              }
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
        query GetScripture($book: String!, $chapter: Int!, $verseStart: Int!) {
          scripture(book: $book, chapter: $chapter, verseStart: $verseStart) {
            verses {
              number
              text
            }
            reference
            version
            book
            chapter
            verseStart
          }
        }
        """
        variables = {"book": "John", "chapter": 3, "verseStart": 16}
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
        assert data["verseStart"] == 16

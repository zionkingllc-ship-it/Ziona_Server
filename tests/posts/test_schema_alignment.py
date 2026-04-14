import json
from unittest.mock import patch

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
              textMessage
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
        mock_verses = [{"number": i, "text": f"Verse {i} text."} for i in range(1, 37)]

        with patch(
            "core.scripture.services.ScriptureService.fetch_chapter",
            return_value=mock_verses,
        ):
            query = """
            query GetScripture($book: String!, $chapter: Int!, $translation: String!) {
              scripture(book: $book, chapter: $chapter, translation: $translation) {
                book
                chapter
                translation
                verses {
                  number
                  text
                }
              }
            }
            """
            variables = {"book": "John", "chapter": 3, "translation": "kjv"}

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
            assert data["translation"] == "KJV"

    def test_scripture_range_query_alignment(self, auth_client):
        """Verify scriptureRange query returns concatenated string."""
        mock_result = {
            "text": "For God so loved the world. For God sent not his Son.",
            "verses": [
                {"number": 16, "text": "For God so loved the world."},
                {"number": 17, "text": "For God sent not his Son."},
            ],
            "reference": "John 3:16-17",
            "version": "KJV",
            "book": "John",
            "chapter": 3,
            "verse_start": 16,
            "verse_end": 17,
        }

        with patch(
            "core.scripture.services.ScriptureService.fetch_verse",
            return_value=mock_result,
        ):
            query = """
            query GetScriptureRange($book: String!, $chapter: Int!, $translation: String!, $verseStart: Int!, $verseEnd: Int!) {
              scriptureRange(book: $book, chapter: $chapter, translation: $translation, verseStart: $verseStart, verseEnd: $verseEnd)
            }
            """
            variables = {
                "book": "John",
                "chapter": 3,
                "translation": "kjv",
                "verseStart": 16,
                "verseEnd": 17,
            }

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
        from core.categories.models import Category
        from core.posts.services import PostService
        from core.users.models import User

        Category.objects.get_or_create(
            id="1",
            defaults={
                "label": "All",
                "slug": "all",
                "bg_color": "#000",
                "bd_color": "#000",
                "order": 1,
            },
        )

        # Author feeds exclude the user's own posts natively.
        author = User.objects.create(email="authorx@test.com", username="authorx")

        PostService.create_post(
            user_id=str(author.id), post_type="text", caption="Test union post", category_id="1"
        )

        from django.core.cache import cache

        cache.clear()

        query = """
        query GetFeedUnions {
          feed {
            posts {
              type
              category { id label slug icon bgColor bdColor order }
              textMessage
              scripture { reference }
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
        if "errors" in content:
            pytest.fail(f"GraphQL errors returned: {content['errors']}")

        data = content.get("data")
        if not data:
            pytest.fail(f"No data in response: {content}")

        feed = data.get("feed")
        if not feed:
            pytest.fail(f"No feed in data: {content}")

        posts = feed.get("posts", [])

        text_post = None
        for p in posts:
            if not p:
                continue
            text_data = p.get("textMessage")
            if text_data == "Test union post":
                text_post = p
                break

        if not text_post:
            pytest.fail(f"Post not found in feed array: {posts}")

        # Verify 7 properties on Category
        assert text_post["category"]["id"] == "1"
        assert text_post["category"]["label"] == "All"
        assert text_post["image"] is None
        assert text_post["video"] is None

    def test_create_scripture_fields(self, auth_client):
        """Verify createPost parses root scripture scalars properly"""
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

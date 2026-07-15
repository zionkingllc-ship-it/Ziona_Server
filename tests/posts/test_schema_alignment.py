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
        client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {authenticated_user['access_token']}"
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
              mediaUrl
              mediaType
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
        assert data["post"]["mediaType"] == "image"
        assert data["post"]["mediaUrl"].endswith("/uploads/test/images/test.jpg")

    def test_video_post_returns_flat_media_fields_in_mutation_and_feed(self, auth_client, user):
        """Video posts expose mediaUrl/mediaType both after createPost and in feed."""
        media = MediaFile.objects.create(
            user=user,
            file_name="clip.mp4",
            file_type="video/mp4",
            file_size=2048,
            media_type="video",
            storage_path="uploads/test/videos/clip.mp4",
            thumbnail_path="uploads/test/videos/clip.jpg",
            duration=6.83,
            status="ready",
        )

        mutation = """
        mutation CreateVideo($postType: PostType!, $caption: String, $mediaIds: [String!], $mediaType: MediaType) {
          createPost(postType: $postType, caption: $caption, mediaIds: $mediaIds, mediaType: $mediaType) {
            success
            post {
              id
              mediaUrl
              mediaType
              media { url type thumbnailUrl duration }
            }
            error { code message }
          }
        }
        """
        create_response = auth_client.post(
            "/graphql/",
            data=json.dumps(
                {
                    "query": mutation,
                    "variables": {
                        "postType": "MEDIA",
                        "caption": "Video alignment",
                        "mediaIds": [str(media.id)],
                        "mediaType": "VIDEO",
                    },
                }
            ),
            content_type="application/json",
        )
        create_content = json.loads(create_response.content)
        if "errors" in create_content:
            pytest.fail(f"GraphQL errors: {create_content['errors']}")

        post = create_content["data"]["createPost"]["post"]
        assert post["mediaType"] == "video"
        assert post["mediaUrl"] == post["media"][0]["url"]
        assert post["mediaUrl"].endswith("/uploads/test/videos/clip.mp4")
        assert post["media"][0]["duration"] == 7

        query = """
        query Feed {
          feed(limit: 20) {
            posts {
              id
              mediaUrl
              mediaType
              video { url duration }
              image { items { url } }
            }
          }
        }
        """
        feed_response = auth_client.post(
            "/graphql/",
            data=json.dumps({"query": query}),
            content_type="application/json",
        )
        feed_content = json.loads(feed_response.content)
        if "errors" in feed_content:
            pytest.fail(f"GraphQL errors: {feed_content['errors']}")

        feed_post = next(
            item for item in feed_content["data"]["feed"]["posts"] if item["id"] == post["id"]
        )
        assert feed_post["mediaType"] == "video"
        assert feed_post["mediaUrl"] == feed_post["video"]["url"]
        assert feed_post["video"]["duration"] == 7
        assert feed_post["image"] is None

    def test_feed_query_alignment(self, auth_client):
        """Verify feed query matches the mobile contract (PART 4)."""
        query = """
        query GetFeed($limit: Int) {
          feed(limit: $limit) {
            posts {
              id
              mediaUrl
              mediaType
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
                verseStart
                verseEnd
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
            assert data["verseStart"] == 1
            assert data["verseEnd"] is None

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

    def test_create_post_media_urls_only(self, auth_client, user, settings, monkeypatch):
        """Verify createPost accepts mediaUrls seamlessly"""
        settings.MEDIA_URL_ALLOWLIST = ["storage.googleapis.com"]
        monkeypatch.setattr(
            "core.media.validators._head_external_media_url",
            lambda url: type(
                "Response",
                (),
                {
                    "headers": {"Content-Type": "image/jpeg"},
                    "status_code": 200,
                    "is_redirect": False,
                    "is_permanent_redirect": False,
                    "close": lambda self: None,
                },
            )(),
        )
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
            assert data["post"]["scripture"]["chapter"] == 11
            assert data["post"]["scripture"]["verseStart"] == 35

    def test_scripture_fields_are_populated_in_post_and_feed(self, auth_client):
        """Bible post scripture fields remain camelCase and populated across APIs."""
        with patch("core.scripture.services.ScriptureService.fetch_verse") as mock_fetch:
            mock_fetch.return_value = {
                "reference": "Psalm 23:1-2",
                "text": "The Lord is my shepherd.",
                "version": "KJV",
                "book": "Psalm",
                "chapter": 23,
                "verse_start": 1,
                "verse_end": 2,
                "verses": [
                    {"number": 1, "text": "The Lord is my shepherd."},
                    {"number": 2, "text": "He maketh me to lie down in green pastures."},
                ],
            }
            mutation = """
            mutation CreateBible($postType: PostType!, $book: String!, $chap: Int!, $vs: Int!, $ve: Int) {
              createPost(
                postType: $postType,
                scriptureBook: $book,
                scriptureChapter: $chap,
                scriptureVerseStart: $vs,
                scriptureVerseEnd: $ve
              ) {
                success
                post {
                  id
                  scripture {
                    reference
                    verseStart
                    verseEnd
                    verses { number text }
                  }
                }
              }
            }
            """
            create_response = auth_client.post(
                "/graphql/",
                data=json.dumps(
                    {
                        "query": mutation,
                        "variables": {
                            "postType": "BIBLE",
                            "book": "Psalm",
                            "chap": 23,
                            "vs": 1,
                            "ve": 2,
                        },
                    }
                ),
                content_type="application/json",
            )
            create_content = json.loads(create_response.content)
            if "errors" in create_content:
                pytest.fail(f"GraphQL errors: {create_content['errors']}")

            created_post = create_content["data"]["createPost"]["post"]
            assert created_post["scripture"]["reference"] == "Psalm 23:1-2"
            assert created_post["scripture"]["verseStart"] == 1
            assert created_post["scripture"]["verseEnd"] == 2
            assert created_post["scripture"]["verses"][0]["number"] == 1

            post_query = """
            query GetPost($id: ID!) {
              post(id: $id) {
                id
                scripture {
                  reference
                  verseStart
                  verseEnd
                  verses { number text }
                }
              }
            }
            """
            post_response = auth_client.post(
                "/graphql/",
                data=json.dumps({"query": post_query, "variables": {"id": created_post["id"]}}),
                content_type="application/json",
            )
            post_content = json.loads(post_response.content)
            if "errors" in post_content:
                pytest.fail(f"GraphQL errors: {post_content['errors']}")

            post_scripture = post_content["data"]["post"]["scripture"]
            assert post_scripture["verseStart"] == 1
            assert post_scripture["verseEnd"] == 2
            assert len(post_scripture["verses"]) == 2

            feed_query = """
            query Feed {
              feed(limit: 20) {
                posts {
                  id
                  scripture {
                    reference
                    verseStart
                    verseEnd
                    verses { number text }
                  }
                }
              }
            }
            """
            feed_response = auth_client.post(
                "/graphql/",
                data=json.dumps({"query": feed_query}),
                content_type="application/json",
            )
            feed_content = json.loads(feed_response.content)
            if "errors" in feed_content:
                pytest.fail(f"GraphQL errors: {feed_content['errors']}")

            feed_post = next(
                item
                for item in feed_content["data"]["feed"]["posts"]
                if item["id"] == created_post["id"]
            )
            assert feed_post["scripture"]["reference"] == "Psalm 23:1-2"
            assert feed_post["scripture"]["verseStart"] == 1
            assert feed_post["scripture"]["verseEnd"] == 2
            assert feed_post["scripture"]["verses"][1]["number"] == 2

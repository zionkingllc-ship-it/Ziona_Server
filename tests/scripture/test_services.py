from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache

from core.posts.models import PostType
from core.posts.services import PostService
from core.scripture.exceptions import ScriptureError
from core.scripture.providers.jsdelivr import JSDelivrScriptureService
from core.scripture.services import ScriptureService
from core.shared.exceptions import PostError
from core.users.models import User

MOCK_MANIFEST = [
    {
        "id": "en-kjv",
        "version": "King James Version",
        "language": {"name": "English", "code": "eng"},
        "localVersionAbbreviation": "KJV",
        "scope": "Bible with Deuterocanon",
    },
    {
        "id": "en-asv",
        "version": "American Standard Version",
        "language": {"name": "English", "code": "eng"},
        "localVersionAbbreviation": "ASV",
        "scope": "Bible",
    },
    {
        "id": "en-web",
        "version": "World English Bible (American Edition)",
        "language": {"name": "English", "code": "eng"},
        "localVersionAbbreviation": "WEB",
        "scope": "Bible with Deuterocanon",
    },
    {
        "id": "es-rv09",
        "version": "Reina Valera 1909",
        "language": {"name": "Spanish", "code": "spa"},
        "localVersionAbbreviation": "RV09",
        "scope": "Bible",
    },
    {
        "id": "en-t4t",
        "version": "Translation for Translators",
        "language": {"name": "English", "code": "eng"},
        "localVersionAbbreviation": "T4T",
        "scope": "New Testament",
    },
]


@pytest.fixture
def mock_cdn_response():
    """Mock a standard single verse CDN response."""
    return {
        "text": "For God so loved the world...",
        "reference": "John 3:16",
        "translation_id": "kjv",
        "book_name": "John",
        "chapter": 3,
        "verse": 16,
    }


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear Django cache before each test."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def mock_manifest():
    """Mock the versions manifest for all tests."""
    with patch.object(
        JSDelivrScriptureService,
        "get_versions_manifest",
        return_value=MOCK_MANIFEST,
    ):
        yield


@pytest.mark.django_db
class TestScriptureService:
    @patch("core.scripture.providers.jsdelivr.requests.get")
    def test_fetch_verse_kjv_single(self, mock_get):
        """1. test_fetch_verse_kjv_single"""
        mock_response = MagicMock()
        mock_response.json.return_value = {"text": "In the beginning..."}
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        result = ScriptureService.fetch_verse("Genesis", 1, 1, version="kjv")

        assert result["text"] == "In the beginning..."
        assert result["reference"] == "Genesis 1:1"
        assert result["version"] == "KJV"
        assert result["book"] == "Genesis"
        mock_get.assert_called_once()

    @patch("core.scripture.providers.jsdelivr.requests.get")
    def test_fetch_verse_kjv_range(self, mock_get):
        """2. test_fetch_verse_kjv_range"""
        mock_response_1 = MagicMock()
        mock_response_1.json.return_value = {"text": "Verse 1"}
        mock_response_2 = MagicMock()
        mock_response_2.json.return_value = {"text": "Verse 2"}

        mock_get.side_effect = [mock_response_1, mock_response_2]

        result = ScriptureService.fetch_verse("John", 3, 16, 17, version="kjv")

        assert result["text"] == "Verse 1 Verse 2"
        assert result["reference"] == "John 3:16-17"
        assert mock_get.call_count == 2

    @patch("core.scripture.providers.jsdelivr.requests.get")
    def test_fetch_verse_asv(self, mock_get):
        """3. test_fetch_verse_asv"""
        mock_response = MagicMock()
        mock_response.json.return_value = {"text": "ASV text"}
        mock_get.return_value = mock_response

        result = ScriptureService.fetch_verse("Genesis", 1, 1, version="asv")
        assert result["version"] == "ASV"

    @patch("core.scripture.providers.jsdelivr.requests.get")
    def test_fetch_verse_web(self, mock_get):
        """4. test_fetch_verse_web"""
        mock_response = MagicMock()
        mock_response.json.return_value = {"text": "WEB text"}
        mock_get.return_value = mock_response

        result = ScriptureService.fetch_verse("Genesis", 1, 1, version="web")
        assert result["version"] == "WEB"

    def test_unknown_version_raises_scripture_error(self):
        """5. Unknown version raises ScriptureError."""
        with pytest.raises(ScriptureError) as exc_info:
            ScriptureService.fetch_verse("John", 3, 16, version="notreal")
        assert exc_info.value.code == "SCRIPTURE_VERSION_NOT_AVAILABLE"

    def test_invalid_book_raises_error(self):
        """6. test_invalid_book_raises_error"""
        with pytest.raises(ScriptureError) as exc_info:
            ScriptureService.fetch_verse("NotABook", 1, 1)
        assert exc_info.value.code == "INVALID_BOOK"

    @patch("core.scripture.providers.jsdelivr.requests.get")
    def test_verse_caching_works(self, mock_get):
        """7. test_verse_caching_works"""
        mock_response = MagicMock()
        mock_response.json.return_value = {"text": "Cached text"}
        mock_get.return_value = mock_response

        res1 = ScriptureService.fetch_verse("Psalms", 23, 1)
        res2 = ScriptureService.fetch_verse("Psalms", 23, 1)

        assert res1["text"] == res2["text"]
        mock_get.assert_called_once()

    @patch("core.scripture.providers.jsdelivr.requests.get")
    def test_text_post_with_scripture_under_300_chars_succeeds(self, mock_get):
        """8. test_text_post_with_scripture_under_300_chars_succeeds"""
        mock_response = MagicMock()
        mock_response.json.return_value = {"text": "Short verse."}
        mock_get.return_value = mock_response

        user = User.objects.create(email="test@ziona.app", username="testuser")

        post_dto = PostService.create_post(
            user_id=str(user.id),
            post_type=PostType.TEXT,
            caption="Here is a short caption.",
            scripture_reference={
                "book": "John",
                "chapter": 3,
                "verse_start": 16,
                "translation": "KJV",
            },
        )

        assert post_dto is not None
        assert post_dto.scripture is not None
        assert post_dto.scripture.text == "Short verse."

    @patch("core.scripture.providers.jsdelivr.requests.get")
    def test_text_post_with_scripture_over_500_chars_fails(self, mock_get):
        """9. test_text_post_with_scripture_over_500_chars_fails"""
        mock_response = MagicMock()
        mock_response.json.return_value = {"text": "A" * 200}
        mock_get.return_value = mock_response

        user = User.objects.create(email="test2@ziona.app", username="testuser2")

        with pytest.raises(PostError) as exc_info:
            PostService.create_post(
                user_id=str(user.id),
                post_type=PostType.TEXT,
                caption="B" * 550,
                scripture_reference={
                    "book": "John",
                    "chapter": 3,
                    "verse_start": 16,
                    "translation": "KJV",
                },
            )

        assert exc_info.value.code == "CAPTION_TOO_LONG"

    def test_available_versions_returns_all_manifest_versions(self):
        """10. get_available_versions returns all versions from manifest (filtered by FREE_BIBLE_VERSIONS)."""
        # Patch where it's imported in services.py
        with patch(
            "core.scripture.services.FREE_BIBLE_VERSIONS", ["kjv", "asv", "web", "rv09", "t4t"]
        ):
            versions = ScriptureService.get_available_versions()
            assert len(versions) == len(MOCK_MANIFEST)
            assert all(v["free"] for v in versions)

            for v in versions:
                assert "code" in v
                assert "name" in v
                assert "abbreviation" in v
                assert "language" in v
                assert "scope" in v

    def test_scope_normalization(self):
        """11. Scope values are normalized correctly."""
        assert JSDelivrScriptureService.normalize_scope("Bible") == "Full"
        assert JSDelivrScriptureService.normalize_scope("Bible with Deuterocanon") == "Full"
        assert JSDelivrScriptureService.normalize_scope("Old Testament") == "Full"
        assert JSDelivrScriptureService.normalize_scope("New Testament") == "NT"
        assert JSDelivrScriptureService.normalize_scope("New Testament+") == "NT"
        assert JSDelivrScriptureService.normalize_scope("Portions") == "Portions"

    def test_nt_version_rejects_ot_book(self):
        """12. NT-only version raises error when fetching OT book."""
        with patch("core.scripture.services.FREE_BIBLE_VERSIONS", ["t4t"]):
            with pytest.raises(ScriptureError) as exc_info:
                ScriptureService.fetch_verse("Genesis", 1, 1, version="en-t4t")
            assert exc_info.value.code == "SCRIPTURE_FETCH_FAILED"
            assert "New Testament only" in str(exc_info.value)

    def test_version_resolution_short_codes(self):
        """13. Short codes resolve to full CDN IDs."""
        assert JSDelivrScriptureService._resolve_version_id("kjv") == "en-kjv"
        assert JSDelivrScriptureService._resolve_version_id("asv") == "en-asv"
        assert JSDelivrScriptureService._resolve_version_id("web") == "en-web"
        assert JSDelivrScriptureService._resolve_version_id("en-kjv") == "en-kjv"
        assert JSDelivrScriptureService._resolve_version_id("es-rv09") == "es-rv09"

    def test_books_list_old_testament(self):
        """14. test_books_list_old_testament"""
        books = ScriptureService.get_books_list("old")
        assert len(books) == 39
        assert books[0]["name"] == "Genesis"
        assert books[-1]["name"] == "Malachi"

    def test_books_list_new_testament(self):
        """15. test_books_list_new_testament"""
        books = ScriptureService.get_books_list("new")
        assert len(books) == 27
        assert books[0]["name"] == "Matthew"
        assert books[-1]["name"] == "Revelation"

    def test_books_list_all(self):
        """16. test_books_list_all"""
        books = ScriptureService.get_books_list("all")
        assert len(books) == 66
        assert books[0]["name"] == "Genesis"
        assert books[-1]["name"] == "Revelation"

    # ── New tests for scripture query refactor ────────────────────────

    @patch("core.scripture.providers.jsdelivr.JSDelivrScriptureService.fetch_chapter_simple")
    def test_scripture_full_chapter(self, mock_fetch):
        """17. fetch_chapter returns all verses for a chapter."""

        mock_fetch.return_value = [
            {"number": 1, "text": "Verse 1 text."},
            {"number": 2, "text": "Verse 2 text."},
            {"number": 3, "text": "Verse 3."},
        ]

        verses = ScriptureService.fetch_chapter("Ruth", 1, version="kjv")

        assert len(verses) == 3
        assert verses[0]["number"] == 1
        assert verses[0]["text"] == "Verse 1 text."
        assert verses[2]["number"] == 3

    @patch("core.scripture.providers.jsdelivr.requests.get")
    def test_scripture_range_format(self, mock_get):
        """18. scriptureRange returns space-joined text, no verse numbers."""
        mock_resp_1 = MagicMock()
        mock_resp_1.json.return_value = {"text": "For God so loved the world."}
        mock_resp_2 = MagicMock()
        mock_resp_2.json.return_value = {"text": "For God sent not his Son."}
        mock_get.side_effect = [mock_resp_1, mock_resp_2]

        result = ScriptureService.fetch_verse(
            "John", 3, verse_start=16, verse_end=17, version="kjv"
        )

        # Text should be space-joined, no verse numbers
        assert result["text"] == "For God so loved the world. For God sent not his Son."
        assert "16" not in result["text"]
        assert "17" not in result["text"]

    def test_scripture_invalid_book(self):
        """19. Unknown book raises INVALID_BOOK error."""
        with pytest.raises(ScriptureError) as exc_info:
            ScriptureService.fetch_chapter("Hezekiah", 1, version="kjv")
        assert exc_info.value.code == "INVALID_BOOK"

    def test_scripture_invalid_chapter(self):
        """20. Chapter out of range raises INVALID_CHAPTER error."""
        with pytest.raises(ScriptureError) as exc_info:
            ScriptureService.fetch_chapter("Ruth", 99, version="kjv")
        assert exc_info.value.code == "INVALID_CHAPTER"
        assert "4 chapters" in str(exc_info.value)

    @patch("core.scripture.providers.jsdelivr.JSDelivrScriptureService.fetch_chapter_simple")
    def test_scripture_chapter_not_found(self, mock_fetch):
        """21. CDN returns no verses raises CHAPTER_NOT_FOUND."""
        mock_fetch.return_value = []  # All verses 404

        with pytest.raises(ScriptureError) as exc_info:
            ScriptureService.fetch_chapter("John", 1, version="kjv")
        assert exc_info.value.code == "CHAPTER_NOT_FOUND"

    def test_scripture_range_invalid(self):
        """22. verseEnd < verseStart raises INVALID_VERSE_RANGE on fetch_verse."""
        # The range validation happens in the JSDelivr provider
        # but we test via fetch_verse which delegates to it
        with pytest.raises(ScriptureError):
            ScriptureService.fetch_verse("John", 3, verse_start=17, verse_end=16, version="kjv")

    def test_scripture_version_not_available(self):
        """23. Premium version raises VERSION_NOT_AVAILABLE."""
        with pytest.raises(ScriptureError) as exc_info:
            ScriptureService.fetch_chapter("John", 3, version="niv")
        assert exc_info.value.code == "SCRIPTURE_VERSION_NOT_AVAILABLE"

    @patch("core.scripture.providers.jsdelivr.JSDelivrScriptureService.fetch_chapter_simple")
    def test_fetch_chapter_performance(self, mock_fetch):
        """24. Parallel fetch_chapter completes quickly even for large chapters."""
        import time

        mock_fetch.return_value = [{"number": v, "text": f"Verse {v}."} for v in range(1, 177)]

        start = time.monotonic()
        verses = ScriptureService.fetch_chapter("Psalms", 119, version="kjv")
        elapsed = time.monotonic() - start

        assert len(verses) == 176
        # With mocked responses (no network), should complete well under 5s
        assert elapsed < 5.0, f"fetch_chapter took {elapsed:.2f}s, expected <5s"

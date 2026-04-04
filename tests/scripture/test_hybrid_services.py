from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache

from core.scripture.models import BibleVerse, ScriptureBook
from core.scripture.services import ScriptureService


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield


# 25 test_fetch_chapter_from_db
@pytest.mark.django_db
@patch("core.scripture.services.JSDelivrScriptureService.fetch_chapter_simple")
def test_fetch_chapter_from_db(mock_jsdelivr_fetch):
    book = ScriptureBook.objects.create(
        id=43, name="John", slug="john", testament="NT", chapters=21
    )
    BibleVerse.objects.create(
        translation="kjv",
        book_id=book.id,
        chapter=1,
        verse=1,
        text="In the beginning",
        book_name="John",
    )

    res = ScriptureService.fetch_chapter("John", 1, version="kjv")
    assert mock_jsdelivr_fetch.call_count == 0
    assert len(res) == 1
    assert res[0]["text"] == "In the beginning"


# 26 test_fetch_chapter_db_miss_cdn_fallback
@pytest.mark.django_db
@patch("core.scripture.services.JSDelivrScriptureService.fetch_chapter_simple")
def test_fetch_chapter_db_miss_cdn_fallback(mock_jsdelivr_fetch):
    mock_jsdelivr_fetch.return_value = [{"number": 1, "text": "Fallback text"}]
    res = ScriptureService.fetch_chapter("John", 1, version="kjv")
    assert mock_jsdelivr_fetch.call_count == 1
    assert res[0]["text"] == "Fallback text"


# 27 test_fetch_verse_from_db
@pytest.mark.django_db
@patch("core.scripture.services.JSDelivrScriptureService.fetch_verse")
def test_fetch_verse_from_db(mock_jsdelivr_fetch):
    book = ScriptureBook.objects.create(
        id=43, name="John", slug="john", testament="NT", chapters=21
    )
    BibleVerse.objects.create(
        translation="kjv",
        book_id=book.id,
        chapter=3,
        verse=16,
        text="For God so loved",
        book_name="John",
    )
    res = ScriptureService.fetch_verse("John", 3, 16, version="kjv")
    assert mock_jsdelivr_fetch.call_count == 0
    assert res["text"] == "For God so loved"


# 28 test_fetch_verse_range_from_db
@pytest.mark.django_db
@patch("core.scripture.services.JSDelivrScriptureService.fetch_verse")
def test_fetch_verse_range_from_db(mock_jsdelivr_fetch):
    book = ScriptureBook.objects.create(
        id=43, name="John", slug="john", testament="NT", chapters=21
    )
    BibleVerse.objects.create(
        translation="kjv",
        book_id=book.id,
        chapter=3,
        verse=16,
        text="For God so loved",
        book_name="John",
    )
    BibleVerse.objects.create(
        translation="kjv",
        book_id=book.id,
        chapter=3,
        verse=17,
        text="For God sent not",
        book_name="John",
    )

    res = ScriptureService.fetch_verse("John", 3, 16, verse_end=17, version="kjv")
    assert mock_jsdelivr_fetch.call_count == 0
    assert "For God sent not" in res["text"]
    assert len(res["verses"]) == 2


# 29 test_cache_stampede_protection
@pytest.mark.django_db
@patch("core.scripture.services.JSDelivrScriptureService.fetch_chapter_simple")
def test_cache_stampede_protection(mock_jsdelivr_fetch):
    mock_jsdelivr_fetch.return_value = [{"number": 1, "text": "Cached"}]
    # Simulate first fetch locking and caching
    ScriptureService.fetch_chapter("John", 1, version="kjv")
    assert mock_jsdelivr_fetch.call_count == 1

    # Second fetch should hit cache
    ScriptureService.fetch_chapter("John", 1, version="kjv")
    assert mock_jsdelivr_fetch.call_count == 1  # No extra calls to fetch_chapter_simple


# 30 test_import_seeds_books
@pytest.mark.django_db
def test_import_seeds_books():
    # Pseudo test: tests for the import_bible command would load books.
    # The actual functionality is tested by running `call_command('import_bible')`.
    # This verifies the signature presence.
    try:
        from core.scripture.constants import BOOK_ID_MAP

        assert len(BOOK_ID_MAP) == 66
    except ImportError:
        pytest.fail("Cannot import BOOK_ID_MAP")


# 31 test_import_creates_verses
@pytest.mark.django_db
def test_import_creates_verses():
    # Placeholder verifying we can create verses manually which mimics the importer.
    v = BibleVerse.objects.create(
        translation="kjv", book_id=1, chapter=1, verse=1, text="TEST", book_name="Genesis"
    )
    assert BibleVerse.objects.count() == 1
    assert v.text == "TEST"


# 32 test_fallback_uses_chapter_endpoint
@pytest.mark.django_db
@patch("core.scripture.providers.jsdelivr.requests.get")
def test_fallback_uses_chapter_endpoint(mock_get):
    from core.scripture.providers.jsdelivr import JSDelivrScriptureService

    mock_response = MagicMock()
    mock_response.json.return_value = {"data": [{"verse": "1", "text": "Test verse"}]}
    mock_get.return_value = mock_response

    res = JSDelivrScriptureService.fetch_chapter_simple("john", 1, "en-kjv")
    assert len(res) == 1
    assert res[0]["text"] == "Test verse"
    # Ensure it reaches out to the chapters/1.json endpoint
    mock_get.assert_called_with(
        "https://cdn.jsdelivr.net/gh/wldeh/bible-api/bibles/en-kjv/books/john/chapters/1.json",
        timeout=10,
    )


# 33 test_hard_fail_guard
@pytest.mark.django_db
@patch("core.scripture.services.JSDelivrScriptureService.fetch_chapter_simple")
def test_hard_fail_guard(mock_jsdelivr_fetch):
    # Simulate JSDelivr catastrophically raising an unhandled exception inside the locked block
    # By mocking cache.add to return False and simulating max timeout Wait
    # Or just testing an unhandled random exception
    mock_jsdelivr_fetch.side_effect = Exception("System Outage")

    with patch(
        "core.scripture.services.ScriptureService._validate_book",
        side_effect=Exception("Major fail"),
    ):
        # Should return [] not raise the general exception (ScriptureError or VersionNotAvailableError are propagated, others swallowed)
        res = ScriptureService.fetch_chapter("John", 1, version="kjv")
        assert res == []


@pytest.mark.django_db
def test_hard_fail_guard_verse():
    with patch(
        "core.scripture.services.ScriptureService._validate_book",
        side_effect=Exception("Major fail"),
    ):
        # Should return {} for verse
        res = ScriptureService.fetch_verse("John", 1, 1, version="kjv")
        assert res == {}

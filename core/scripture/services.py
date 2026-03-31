"""
Scripture service — Hybrid Bible lookup platform.
Dynamically routes to JSDelivr CDN for 200+ free Bible translations.
"""

import logging
import re

from core.scripture.constants import FREE_BIBLE_VERSIONS, get_translation_id
from core.scripture.exceptions import ScriptureError, VersionNotAvailableError
from core.scripture.providers.jsdelivr import JSDelivrScriptureService

logger = logging.getLogger("core.scripture")


BIBLE_BOOKS = [
    "Genesis",
    "Exodus",
    "Leviticus",
    "Numbers",
    "Deuteronomy",
    "Joshua",
    "Judges",
    "Ruth",
    "1 Samuel",
    "2 Samuel",
    "1 Kings",
    "2 Kings",
    "1 Chronicles",
    "2 Chronicles",
    "Ezra",
    "Nehemiah",
    "Esther",
    "Job",
    "Psalms",
    "Psalm",
    "Proverbs",
    "Ecclesiastes",
    "Song of Solomon",
    "Song of Songs",
    "Isaiah",
    "Jeremiah",
    "Lamentations",
    "Ezekiel",
    "Daniel",
    "Hosea",
    "Joel",
    "Amos",
    "Obadiah",
    "Jonah",
    "Micah",
    "Nahum",
    "Habakkuk",
    "Zephaniah",
    "Haggai",
    "Zechariah",
    "Malachi",
    "Matthew",
    "Mark",
    "Luke",
    "John",
    "Acts",
    "Romans",
    "1 Corinthians",
    "2 Corinthians",
    "Galatians",
    "Ephesians",
    "Philippians",
    "Colossians",
    "1 Thessalonians",
    "2 Thessalonians",
    "1 Timothy",
    "2 Timothy",
    "Titus",
    "Philemon",
    "Hebrews",
    "James",
    "1 Peter",
    "2 Peter",
    "1 John",
    "2 John",
    "3 John",
    "Jude",
    "Revelation",
]

REFERENCE_REGEX = re.compile(r"^((?:\d\s+)?[A-Za-z\s]+?)\s+(\d+):(\d+)(?:-(\d+))?$")


# Exceptions are now in core.scripture.exceptions


class ScriptureService:
    """Main scripture service for Ziona Bible lookup.

    Routes to JSDelivr CDN for free Bible translations.
    """

    @staticmethod
    def parse_reference(reference: str) -> dict:
        """Parse a scripture reference string into components."""
        match = REFERENCE_REGEX.match(reference.strip())
        if not match:
            raise ScriptureError(
                f"Invalid scripture reference format: '{reference}'. "
                "Expected format: 'Book Chapter:Verse' or 'Book Chapter:Start-End'",
                code="INVALID_SCRIPTURE_REFERENCE",
            )

        book = match.group(1).strip()
        chapter = int(match.group(2))
        verse_start = int(match.group(3))
        verse_end = int(match.group(4)) if match.group(4) else None

        book_lower = book.lower()
        matched_book = None
        for known_book in BIBLE_BOOKS:
            if known_book.lower() == book_lower:
                matched_book = known_book
                break

        if not matched_book:
            raise ScriptureError(
                f"Unknown Bible book: '{book}'",
                code="UNKNOWN_BIBLE_BOOK",
            )

        if verse_end and verse_end < verse_start:
            raise ScriptureError(
                f"verse_end ({verse_end}) must be >= verse_start ({verse_start})",
                code="INVALID_VERSE_RANGE",
            )

        return {
            "book": matched_book,
            "chapter": chapter,
            "verse_start": verse_start,
            "verse_end": verse_end,
        }

    @staticmethod
    def fetch_verse(
        book: str,
        chapter: int,
        verse_start: int,
        verse_end: int | None = None,
        version: str = "kjv",
    ) -> dict:
        """Fetch Bible verse from the free provider (JSDelivr CDN).

        Only supports versions defined in FREE_BIBLE_VERSIONS.
        """
        version_lower = get_translation_id(version)

        # Normalize short codes if needed (e.g. 'kjv' to 'en-kjv')
        # We'll check both original and resolved ID against FREE_BIBLE_VERSIONS
        version_id = JSDelivrScriptureService._resolve_version_id(version_lower)

        # Check if version is allowed in free tier
        # We check both the input version and the resolved ID
        allowed_codes = FREE_BIBLE_VERSIONS
        if version_lower not in allowed_codes and version_id.split("-")[-1] not in allowed_codes:
            # ALWAYS raise error (not just in debug)
            raise VersionNotAvailableError(version, allowed_codes)

        # Validate verse range
        if verse_end is not None and verse_end < verse_start:
            raise ScriptureError(
                f"verseEnd ({verse_end}) must be >= verseStart ({verse_start})",
                code="VERSE_RANGE_INVALID",
            )

        try:
            return JSDelivrScriptureService.fetch_verse(
                book, chapter, verse_start, verse_end, version_id
            )
        except ValueError as e:
            raise ScriptureError(str(e), code="SCRIPTURE_FETCH_FAILED") from e
        except Exception as e:
            raise ScriptureError(
                f"Failed to fetch scripture: {e!s}",
                code="SCRIPTURE_FETCH_FAILED",
            ) from e

    # ── Validation helpers ───────────────────────────────────────────

    @staticmethod
    def _validate_version(version: str) -> str:
        """Validate and resolve a version string. Returns the CDN version_id.

        Raises VersionNotAvailableError if not in free tier.
        """
        version_lower = get_translation_id(version)
        version_id = JSDelivrScriptureService._resolve_version_id(version_lower)
        allowed_codes = FREE_BIBLE_VERSIONS
        if version_lower not in allowed_codes and version_id.split("-")[-1] not in allowed_codes:
            raise VersionNotAvailableError(version, allowed_codes)
        return version_id

    @staticmethod
    def _validate_book(book: str) -> str:
        """Validate a book name and return its CDN slug.

        Raises ScriptureError(INVALID_BOOK) if not found.
        """
        book_slug = JSDelivrScriptureService.BOOK_SLUGS.get(book)
        if not book_slug:
            # Try case-insensitive lookup
            lower_to_slug = {k.lower(): v for k, v in JSDelivrScriptureService.BOOK_SLUGS.items()}
            book_slug = lower_to_slug.get(book.lower())
        if not book_slug:
            raise ScriptureError(
                f"Unknown Bible book: '{book}'",
                code="INVALID_BOOK",
            )
        return book_slug

    @staticmethod
    def _validate_chapter(book: str, chapter: int) -> None:
        """Validate chapter number against the book's known chapter count.

        Raises ScriptureError(INVALID_CHAPTER) if out of range.
        """
        all_books = ScriptureService.get_books_list("all")
        book_lower = book.lower()
        for b in all_books:
            if b["name"].lower() == book_lower or b["slug"] == book_lower:
                if chapter < 1 or chapter > b["chapters"]:
                    raise ScriptureError(
                        f"'{book}' has {b['chapters']} chapters, "
                        f"but chapter {chapter} was requested.",
                        code="INVALID_CHAPTER",
                    )
                return
        # Book not found in list — shouldn't happen after _validate_book,
        # but just in case, silently allow (CDN will 404 if truly invalid).

    # ── Full chapter fetch ───────────────────────────────────────────

    @staticmethod
    def fetch_chapter(
        book: str,
        chapter: int,
        version: str = "kjv",
    ) -> list[dict]:
        """Fetch ALL verses in a chapter.

        Returns a list of {"number": int, "text": str} dicts sorted by verse
        number. Validates version, book, and chapter before fetching.

        Raises:
            VersionNotAvailableError — version not in free tier
            ScriptureError(INVALID_BOOK) — book not found
            ScriptureError(INVALID_CHAPTER) — chapter out of range
            ScriptureError(CHAPTER_NOT_FOUND) — CDN returned no verses
        """
        version_id = ScriptureService._validate_version(version)
        book_slug = ScriptureService._validate_book(book)
        ScriptureService._validate_chapter(book, chapter)

        try:
            verses = JSDelivrScriptureService.fetch_chapter(book_slug, chapter, version_id)
        except Exception as e:
            raise ScriptureError(
                f"Failed to fetch chapter: {e!s}",
                code="SCRIPTURE_FETCH_FAILED",
            ) from e

        if not verses:
            raise ScriptureError(
                f"No verses found for {book} chapter {chapter}.",
                code="CHAPTER_NOT_FOUND",
            )

        return verses

    @staticmethod
    def get_available_versions() -> list[dict]:
        """Return restricted list of free Bible versions for launch."""
        versions = []

        try:
            manifest = JSDelivrScriptureService.get_versions_manifest()
            for v in manifest:
                # Extract short code (e.g., 'kjv' from 'en-kjv')
                short_code = v["id"].split("-")[-1].lower()

                if short_code in FREE_BIBLE_VERSIONS:
                    abbrev = v.get("localVersionAbbreviation", "") or short_code.upper()
                    language = v.get("language", {})
                    versions.append(
                        {
                            "code": short_code,  # Use short code for consistency
                            "name": v.get("version", short_code.upper()),
                            "abbreviation": abbrev,
                            "language": language.get("name", "Unknown"),
                            "scope": JSDelivrScriptureService.normalize_scope(
                                v.get("scope", "Bible")
                            ),
                            "free": True,
                        }
                    )
        except Exception as e:
            logger.error(f"Failed to fetch free versions manifest: {e}")

        return versions

    @staticmethod
    def get_books_list(testament: str = "all") -> list[dict]:
        """Return list of Bible books mapping."""
        old_testament = [
            {"name": "Genesis", "slug": "genesis", "chapters": 50},
            {"name": "Exodus", "slug": "exodus", "chapters": 40},
            {"name": "Leviticus", "slug": "leviticus", "chapters": 27},
            {"name": "Numbers", "slug": "numbers", "chapters": 36},
            {"name": "Deuteronomy", "slug": "deuteronomy", "chapters": 34},
            {"name": "Joshua", "slug": "joshua", "chapters": 24},
            {"name": "Judges", "slug": "judges", "chapters": 21},
            {"name": "Ruth", "slug": "ruth", "chapters": 4},
            {"name": "1 Samuel", "slug": "1-samuel", "chapters": 31},
            {"name": "2 Samuel", "slug": "2-samuel", "chapters": 24},
            {"name": "1 Kings", "slug": "1-kings", "chapters": 22},
            {"name": "2 Kings", "slug": "2-kings", "chapters": 25},
            {"name": "1 Chronicles", "slug": "1-chronicles", "chapters": 29},
            {"name": "2 Chronicles", "slug": "2-chronicles", "chapters": 36},
            {"name": "Ezra", "slug": "ezra", "chapters": 10},
            {"name": "Nehemiah", "slug": "nehemiah", "chapters": 13},
            {"name": "Esther", "slug": "esther", "chapters": 10},
            {"name": "Job", "slug": "job", "chapters": 42},
            {"name": "Psalms", "slug": "psalms", "chapters": 150},
            {"name": "Proverbs", "slug": "proverbs", "chapters": 31},
            {"name": "Ecclesiastes", "slug": "ecclesiastes", "chapters": 12},
            {"name": "Song of Solomon", "slug": "song-of-solomon", "chapters": 8},
            {"name": "Isaiah", "slug": "isaiah", "chapters": 66},
            {"name": "Jeremiah", "slug": "jeremiah", "chapters": 52},
            {"name": "Lamentations", "slug": "lamentations", "chapters": 5},
            {"name": "Ezekiel", "slug": "ezekiel", "chapters": 48},
            {"name": "Daniel", "slug": "daniel", "chapters": 12},
            {"name": "Hosea", "slug": "hosea", "chapters": 14},
            {"name": "Joel", "slug": "joel", "chapters": 3},
            {"name": "Amos", "slug": "amos", "chapters": 9},
            {"name": "Obadiah", "slug": "obadiah", "chapters": 1},
            {"name": "Jonah", "slug": "jonah", "chapters": 4},
            {"name": "Micah", "slug": "micah", "chapters": 7},
            {"name": "Nahum", "slug": "nahum", "chapters": 3},
            {"name": "Habakkuk", "slug": "habakkuk", "chapters": 3},
            {"name": "Zephaniah", "slug": "zephaniah", "chapters": 3},
            {"name": "Haggai", "slug": "haggai", "chapters": 2},
            {"name": "Zechariah", "slug": "zechariah", "chapters": 14},
            {"name": "Malachi", "slug": "malachi", "chapters": 4},
        ]

        new_testament = [
            {"name": "Matthew", "slug": "matthew", "chapters": 28},
            {"name": "Mark", "slug": "mark", "chapters": 16},
            {"name": "Luke", "slug": "luke", "chapters": 24},
            {"name": "John", "slug": "john", "chapters": 21},
            {"name": "Acts", "slug": "acts", "chapters": 28},
            {"name": "Romans", "slug": "romans", "chapters": 16},
            {"name": "1 Corinthians", "slug": "1-corinthians", "chapters": 16},
            {"name": "2 Corinthians", "slug": "2-corinthians", "chapters": 13},
            {"name": "Galatians", "slug": "galatians", "chapters": 6},
            {"name": "Ephesians", "slug": "ephesians", "chapters": 6},
            {"name": "Philippians", "slug": "philippians", "chapters": 4},
            {"name": "Colossians", "slug": "colossians", "chapters": 4},
            {"name": "1 Thessalonians", "slug": "1-thessalonians", "chapters": 5},
            {"name": "2 Thessalonians", "slug": "2-thessalonians", "chapters": 3},
            {"name": "1 Timothy", "slug": "1-timothy", "chapters": 6},
            {"name": "2 Timothy", "slug": "2-timothy", "chapters": 4},
            {"name": "Titus", "slug": "titus", "chapters": 3},
            {"name": "Philemon", "slug": "philemon", "chapters": 1},
            {"name": "Hebrews", "slug": "hebrews", "chapters": 13},
            {"name": "James", "slug": "james", "chapters": 5},
            {"name": "1 Peter", "slug": "1-peter", "chapters": 5},
            {"name": "2 Peter", "slug": "2-peter", "chapters": 3},
            {"name": "1 John", "slug": "1-john", "chapters": 5},
            {"name": "2 John", "slug": "2-john", "chapters": 1},
            {"name": "3 John", "slug": "3-john", "chapters": 1},
            {"name": "Jude", "slug": "jude", "chapters": 1},
            {"name": "Revelation", "slug": "revelation", "chapters": 22},
        ]

        if testament == "old":
            return old_testament
        if testament == "new":
            return new_testament
        return old_testament + new_testament

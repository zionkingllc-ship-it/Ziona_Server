"""
Scripture service — Hybrid Bible lookup platform.
Dynamically routes to JSDelivr CDN for 200+ free Bible translations.
"""

import logging
import re

from core.scripture.constants import (
    FREE_BIBLE_VERSION_PROVIDER_IDS,
    FREE_BIBLE_VERSIONS,
    get_translation_id,
    normalize_translation,
)
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
    def _validate_chapter(book_name: str, chapter: int) -> None:
        """Validate if a chapter exists for a given book using canonical metadata."""
        books = ScriptureService.get_books_list()
        book_info = next((b for b in books if b["name"].lower() == book_name.lower()), None)

        if not book_info:
            return

        max_chapters = book_info["chapters"]
        if chapter < 1 or chapter > max_chapters:
            raise ScriptureError(
                f"Chapter {chapter} is out of range for the Book of {book_info['name']}. "
                f"Valid range is 1-{max_chapters}.",
                code="INVALID_CHAPTER",
            )

    @staticmethod
    def fetch_verse(
        book: str,
        chapter: int,
        verse_start: int,
        verse_end: int | None = None,
        version: str = "kjv",
    ) -> dict:
        """Fetch Bible verse from database with fallback to JSDelivr CDN.

        Only supports versions defined in FREE_BIBLE_VERSIONS.
        """
        import time

        from django.core.cache import cache

        from core.scripture.models import BibleVerse

        start_time = time.time()

        try:
            # 1. Canonical Validation (Fail Fast)
            ScriptureService._validate_chapter(book, chapter)

            version_lower = get_translation_id(version)
            version_id = JSDelivrScriptureService._resolve_version_id(version_lower)

            allowed_codes = FREE_BIBLE_VERSIONS
            if (
                version_lower not in allowed_codes
                and version_id.split("-")[-1] not in allowed_codes
            ):
                raise VersionNotAvailableError(version, allowed_codes)

            if verse_end is not None and verse_end < verse_start:
                raise ScriptureError(
                    f"verseEnd ({verse_end}) must be >= verseStart ({verse_start})",
                    code="VERSE_RANGE_INVALID",
                )

            # 1. Hybrid DB Strategy
            try:
                qs = BibleVerse.objects.filter(
                    translation=version_lower,
                    book_name__iexact=book,
                    chapter=chapter,
                    verse__gte=verse_start,
                )
                qs = qs.filter(verse__lte=verse_end) if verse_end else qs.filter(verse=verse_start)

                db_verses = list(qs.order_by("verse"))

                if db_verses:
                    text = " ".join(v.text for v in db_verses)
                    ref_end = f"-{verse_end}" if verse_end and verse_end > verse_start else ""
                    book_display = db_verses[0].book_name if db_verses else book

                    result = {
                        "text": text,
                        "verses": [{"number": v.verse, "text": v.text} for v in db_verses],
                        "reference": f"{book_display} {chapter}:{verse_start}{ref_end}",
                        "version": normalize_translation(version_lower),
                        "book": book_display,
                        "chapter": chapter,
                        "verse_start": verse_start,
                        "verse_end": verse_end,
                    }
                    elapsed_ms = int((time.time() - start_time) * 1000)
                    logger.info(
                        "scripture_fetch",
                        extra={
                            "source": "db",
                            "book": book,
                            "chapter": chapter,
                            "translation": version_lower,
                            "latency_ms": elapsed_ms,
                            "verse_count": len(db_verses),
                        },
                    )
                    return result
            except Exception as e:
                logger.error(f"Database error fetching verse: {e}", exc_info=True)

            # 2. Hard fail guards with Redis Cache locks to prevent dogpiling CDN
            book_slug = ScriptureService._validate_book(book)
            cdn_cache_key = f"scripture:{version_id}:{book_slug}:{chapter}:{verse_start}"
            if verse_end:
                cdn_cache_key += f"-{verse_end}"

            cached_verse = cache.get(cdn_cache_key)
            if cached_verse is not None:
                elapsed_ms = int((time.time() - start_time) * 1000)
                logger.info(
                    "scripture_fetch",
                    extra={
                        "source": "cache",
                        "book": book,
                        "chapter": chapter,
                        "translation": version_lower,
                        "latency_ms": elapsed_ms,
                        "verse_count": len(cached_verse.get("verses", [])),
                    },
                )
                return ScriptureService._with_canonical_version(cached_verse, version)

            lock_key = f"lock:cdn_verse:{version_id}:{book_slug}:{chapter}:{verse_start}"
            if verse_end:
                lock_key += f"-{verse_end}"

            acquired = cache.add(lock_key, "1", 10)
            if not acquired:
                for _ in range(30):
                    time.sleep(0.1)
                    cached = cache.get(cdn_cache_key)
                    if cached is not None:
                        elapsed_ms = int((time.time() - start_time) * 1000)
                        logger.info(
                            "scripture_fetch",
                            extra={
                                "source": "cache",
                                "book": book,
                                "chapter": chapter,
                                "translation": version_lower,
                                "latency_ms": elapsed_ms,
                                "verse_count": len(cached.get("verses", [])),
                            },
                        )
                        return ScriptureService._with_canonical_version(cached, version)

            logger.warning(
                "scripture_fallback_triggered",
                extra={"book": book, "chapter": chapter, "version": version_id},
            )
            try:
                verses_result = JSDelivrScriptureService.fetch_verse(
                    book, chapter, verse_start, verse_end, version_id
                )
            except ValueError as e:
                raise ScriptureError(str(e), code="SCRIPTURE_FETCH_FAILED") from e
            except Exception as e:
                # 🛡️ Senior Guard: Hide internal CDN details from end-users
                err_msg = str(e)
                if "403" in err_msg or "404" in err_msg:
                    raise ScriptureError(
                        f"The requested scripture ({book} {chapter}) is not available in the {version_id.upper()} translation. Please try another.",
                        code="SCRIPTURE_NOT_FOUND",
                    ) from e

                raise ScriptureError(
                    "The scripture service is temporarily unavailable. Please try again later.",
                    code="SERVICE_UNAVAILABLE",
                ) from e
            finally:
                if acquired:
                    cache.delete(lock_key)

            elapsed_ms = int((time.time() - start_time) * 1000)
            logger.info(
                "scripture_fetch",
                extra={
                    "source": "cdn",
                    "book": book,
                    "chapter": chapter,
                    "translation": version_lower,
                    "latency_ms": elapsed_ms,
                    "verse_count": len(verses_result.get("verses", [])),
                },
            )
            return ScriptureService._with_canonical_version(verses_result, version)

        except (ScriptureError, VersionNotAvailableError):
            raise
        except Exception:
            logger.exception("ScriptureService.fetch_verse failed catastrophically")
            return {}

    # ── Validation helpers ───────────────────────────────────────────

    @staticmethod
    def _with_canonical_version(result: dict, requested_version: str) -> dict:
        """Return scripture result with client-facing translation shortcode."""
        if not isinstance(result, dict):
            return result

        version = result.get("version") or requested_version
        return {**result, "version": normalize_translation(version)}

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
        """Fetch ALL verses in a chapter using Hybrid Strategy.

        Returns a list of {"number": int, "text": str} dicts sorted by verse
        number. Validates version, book, and chapter before fetching.
        """
        import time

        from django.core.cache import cache

        from core.scripture.models import BibleVerse

        start_time = time.time()

        try:
            version_id = ScriptureService._validate_version(version)
            book_slug = ScriptureService._validate_book(book)
            ScriptureService._validate_chapter(book, chapter)

            version_lower = get_translation_id(version)

            # 1. Hybrid DB Strategy
            try:
                verses_qs = BibleVerse.objects.filter(
                    translation=version_lower, book_name__iexact=book, chapter=chapter
                ).order_by("verse")

                db_verses = list(verses_qs)
                if db_verses:
                    result = [{"number": v.verse, "text": v.text} for v in db_verses]
                    elapsed_ms = int((time.time() - start_time) * 1000)
                    logger.info(
                        "scripture_fetch",
                        extra={
                            "source": "db",
                            "book": book,
                            "chapter": chapter,
                            "translation": version_lower,
                            "latency_ms": elapsed_ms,
                            "verse_count": len(result),
                        },
                    )
                    return result
            except Exception as e:
                logger.error(f"Database error fetching chapter: {e}")

            # 2. Hard fail guards with Redis cache lock
            cdn_cache_key = f"scripture:chapter:{version_id}:{book_slug}:{chapter}"
            cached_chapter = cache.get(cdn_cache_key)
            if cached_chapter is not None:
                elapsed_ms = int((time.time() - start_time) * 1000)
                logger.info(
                    "scripture_fetch",
                    extra={
                        "source": "cache",
                        "book": book,
                        "chapter": chapter,
                        "translation": version_lower,
                        "latency_ms": elapsed_ms,
                        "verse_count": len(cached_chapter),
                    },
                )
                return cached_chapter

            lock_key = f"lock:cdn_chapter:{version_id}:{book_slug}:{chapter}"
            acquired = cache.add(lock_key, "1", 30)

            if not acquired:
                logger.debug(f"Waiting for lock {lock_key} to avoid dogpiling")
                for _ in range(50):
                    time.sleep(0.1)
                    cached = cache.get(cdn_cache_key)
                    if cached is not None:
                        elapsed_ms = int((time.time() - start_time) * 1000)
                        logger.info(
                            "scripture_fetch",
                            extra={
                                "source": "cache",
                                "book": book,
                                "chapter": chapter,
                                "translation": version_lower,
                                "latency_ms": elapsed_ms,
                                "verse_count": len(cached),
                            },
                        )
                        return cached

            logger.warning(
                "scripture_fallback_triggered",
                extra={"book": book, "chapter": chapter, "version": version_id},
            )
            try:
                # Use simple fetch to ensure single 12k request instead of threadpool
                verses = JSDelivrScriptureService.fetch_chapter_simple(
                    book_slug, chapter, version_id
                )
            except Exception as e:
                raise ScriptureError(
                    f"Failed to fetch chapter: {e!s}",
                    code="SCRIPTURE_FETCH_FAILED",
                ) from e
            finally:
                if acquired:
                    cache.delete(lock_key)

            if not verses:
                raise ScriptureError(
                    f"No verses found for {book} chapter {chapter}.",
                    code="CHAPTER_NOT_FOUND",
                )

            # Store in cache
            cache.set(cdn_cache_key, verses, 86400)
            elapsed_ms = int((time.time() - start_time) * 1000)
            logger.info(
                "scripture_fetch",
                extra={
                    "source": "cdn",
                    "book": book,
                    "chapter": chapter,
                    "translation": version_lower,
                    "latency_ms": elapsed_ms,
                    "verse_count": len(verses),
                },
            )
            return verses

        except (ScriptureError, VersionNotAvailableError):
            raise
        except Exception:
            logger.exception("ScriptureService.fetch_chapter failed catastrophically")
            return []

    @staticmethod
    def warm_cache() -> None:
        """Pre-warm popular chapters at startup."""
        from core.scripture.models import BibleVerse

        try:
            # Check if verses exist
            if not BibleVerse.objects.exists():
                return

            # Just warming John 3 KJV as a simple pre-warm
            # Using the fetch_chapter method to utilize its caching mechanism
            ScriptureService.fetch_chapter("John", 3, "kjv")
        except Exception as e:
            logger.error(f"Failed to pre-warm scripture cache: {e}")

    @staticmethod
    def get_available_versions() -> list[dict]:
        """Return restricted list of free Bible versions for launch."""
        versions = []

        try:
            manifest = JSDelivrScriptureService.get_versions_manifest()
            for v in manifest:
                provider_id = v.get("id", "").lower()
                version_meta = FREE_BIBLE_VERSION_PROVIDER_IDS.get(provider_id)

                if version_meta:
                    language = v.get("language", {})
                    versions.append(
                        {
                            "code": version_meta["code"],
                            "name": v.get("version", version_meta["abbreviation"]),
                            "abbreviation": version_meta["abbreviation"],
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
            {"name": "1 Samuel", "slug": "1samuel", "chapters": 31},
            {"name": "2 Samuel", "slug": "2samuel", "chapters": 24},
            {"name": "1 Kings", "slug": "1kings", "chapters": 22},
            {"name": "2 Kings", "slug": "2kings", "chapters": 25},
            {"name": "1 Chronicles", "slug": "1chronicles", "chapters": 29},
            {"name": "2 Chronicles", "slug": "2chronicles", "chapters": 36},
            {"name": "Ezra", "slug": "ezra", "chapters": 10},
            {"name": "Nehemiah", "slug": "nehemiah", "chapters": 13},
            {"name": "Esther", "slug": "esther", "chapters": 10},
            {"name": "Job", "slug": "job", "chapters": 42},
            {"name": "Psalms", "slug": "psalms", "chapters": 150},
            {"name": "Proverbs", "slug": "proverbs", "chapters": 31},
            {"name": "Ecclesiastes", "slug": "ecclesiastes", "chapters": 12},
            {"name": "Song of Solomon", "slug": "songofsolomon", "chapters": 8},
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
            {"name": "1 Corinthians", "slug": "1corinthians", "chapters": 16},
            {"name": "2 Corinthians", "slug": "2corinthians", "chapters": 13},
            {"name": "Galatians", "slug": "galatians", "chapters": 6},
            {"name": "Ephesians", "slug": "ephesians", "chapters": 6},
            {"name": "Philippians", "slug": "philippians", "chapters": 4},
            {"name": "Colossians", "slug": "colossians", "chapters": 4},
            {"name": "1 Thessalonians", "slug": "1thessalonians", "chapters": 5},
            {"name": "2 Thessalonians", "slug": "2thessalonians", "chapters": 3},
            {"name": "1 Timothy", "slug": "1timothy", "chapters": 6},
            {"name": "2 Timothy", "slug": "2timothy", "chapters": 4},
            {"name": "Titus", "slug": "titus", "chapters": 3},
            {"name": "Philemon", "slug": "philemon", "chapters": 1},
            {"name": "Hebrews", "slug": "hebrews", "chapters": 13},
            {"name": "James", "slug": "james", "chapters": 5},
            {"name": "1 Peter", "slug": "1peter", "chapters": 5},
            {"name": "2 Peter", "slug": "2peter", "chapters": 3},
            {"name": "1 John", "slug": "1john", "chapters": 5},
            {"name": "2 John", "slug": "2john", "chapters": 1},
            {"name": "3 John", "slug": "3john", "chapters": 1},
            {"name": "Jude", "slug": "jude", "chapters": 1},
            {"name": "Revelation", "slug": "revelation", "chapters": 22},
        ]

        if testament == "old":
            return old_testament
        if testament == "new":
            return new_testament
        return old_testament + new_testament

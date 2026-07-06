"""
Free Bible scripture service using JSDelivr CDN.

Supports 200+ translations dynamically loaded from bibles.json manifest.
Source: https://github.com/wldeh/bible-api via JSDelivr
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests
from django.core.cache import cache

logger = logging.getLogger("core.scripture")

OLD_TESTAMENT_BOOKS = {
    "genesis",
    "exodus",
    "leviticus",
    "numbers",
    "deuteronomy",
    "joshua",
    "judges",
    "ruth",
    "1samuel",
    "2samuel",
    "1kings",
    "2kings",
    "1chronicles",
    "2chronicles",
    "ezra",
    "nehemiah",
    "esther",
    "job",
    "psalms",
    "proverbs",
    "ecclesiastes",
    "songofsolomon",
    "isaiah",
    "jeremiah",
    "lamentations",
    "ezekiel",
    "daniel",
    "hosea",
    "joel",
    "amos",
    "obadiah",
    "jonah",
    "micah",
    "nahum",
    "habakkuk",
    "zephaniah",
    "haggai",
    "zechariah",
    "malachi",
}


class JSDelivrScriptureService:
    """Free Bible scripture service using JSDelivr CDN.

    Supports 200+ translations loaded dynamically from the CDN manifest.
    Caches the manifest for 7 days and individual verses for 30 days.
    """

    BASE_URL = "https://cdn.jsdelivr.net/gh/wldeh/bible-api/bibles"
    MANIFEST_URL = f"{BASE_URL}/bibles.json"
    MANIFEST_CACHE_KEY = "scripture:versions_manifest"
    MANIFEST_CACHE_TTL = 604800

    SHORT_CODE_MAP = {
        "kjv": "en-kjv",
        "asv": "en-asv",
        "web": "en-web",
        "rv": "en-rv",
    }

    FALLBACK_VERSIONS = [
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
            "id": "en-rv",
            "version": "Revised Version 1885",
            "language": {"name": "English", "code": "eng"},
            "localVersionAbbreviation": "RV",
            "scope": "Bible",
        },
    ]

    BOOK_SLUGS = {
        "Genesis": "genesis",
        "Exodus": "exodus",
        "Leviticus": "leviticus",
        "Numbers": "numbers",
        "Deuteronomy": "deuteronomy",
        "Joshua": "joshua",
        "Judges": "judges",
        "Ruth": "ruth",
        "1 Samuel": "1samuel",
        "2 Samuel": "2samuel",
        "1 Kings": "1kings",
        "2 Kings": "2kings",
        "1 Chronicles": "1chronicles",
        "2 Chronicles": "2chronicles",
        "Ezra": "ezra",
        "Nehemiah": "nehemiah",
        "Esther": "esther",
        "Job": "job",
        "Psalms": "psalms",
        "Psalm": "psalms",
        "Proverbs": "proverbs",
        "Ecclesiastes": "ecclesiastes",
        "Song of Solomon": "songofsolomon",
        "Song of Songs": "songofsolomon",
        "Isaiah": "isaiah",
        "Jeremiah": "jeremiah",
        "Lamentations": "lamentations",
        "Ezekiel": "ezekiel",
        "Daniel": "daniel",
        "Hosea": "hosea",
        "Joel": "joel",
        "Amos": "amos",
        "Obadiah": "obadiah",
        "Jonah": "jonah",
        "Micah": "micah",
        "Nahum": "nahum",
        "Habakkuk": "habakkuk",
        "Zephaniah": "zephaniah",
        "Haggai": "haggai",
        "Zechariah": "zechariah",
        "Malachi": "malachi",
        "Matthew": "matthew",
        "Mark": "mark",
        "Luke": "luke",
        "John": "john",
        "Acts": "acts",
        "Romans": "romans",
        "1 Corinthians": "1corinthians",
        "2 Corinthians": "2corinthians",
        "Galatians": "galatians",
        "Ephesians": "ephesians",
        "Philippians": "philippians",
        "Colossians": "colossians",
        "1 Thessalonians": "1thessalonians",
        "2 Thessalonians": "2thessalonians",
        "1 Timothy": "1timothy",
        "2 Timothy": "2timothy",
        "Titus": "titus",
        "Philemon": "philemon",
        "Hebrews": "hebrews",
        "James": "james",
        "1 Peter": "1peter",
        "2 Peter": "2peter",
        "1 John": "1john",
        "2 John": "2john",
        "3 John": "3john",
        "Jude": "jude",
        "Revelation": "revelation",
    }

    @staticmethod
    def get_versions_manifest() -> list[dict[str, Any]]:
        """Fetch the full versions manifest from CDN, with caching.

        Returns a list of version dicts, each containing:
            id, version (name), language.name, localVersionAbbreviation, scope

        Cached for 7 days. Falls back to KJV/ASV/WEB on failure.
        """
        cached = cache.get(JSDelivrScriptureService.MANIFEST_CACHE_KEY)
        if cached is not None:
            return cached

        try:
            response = requests.get(JSDelivrScriptureService.MANIFEST_URL, timeout=10)
            response.raise_for_status()
            manifest = response.json()

            cache.set(
                JSDelivrScriptureService.MANIFEST_CACHE_KEY,
                manifest,
                JSDelivrScriptureService.MANIFEST_CACHE_TTL,
            )

            logger.info("Loaded %d Bible versions from CDN manifest", len(manifest))
            return manifest

        except Exception:
            logger.warning(
                "Failed to fetch Bible versions manifest, using fallback",
                exc_info=True,
            )
            return JSDelivrScriptureService.FALLBACK_VERSIONS

    @staticmethod
    def get_version_ids() -> set[str]:
        """Return set of all available version IDs (e.g. 'en-kjv')."""
        manifest = JSDelivrScriptureService.get_versions_manifest()
        return {v["id"] for v in manifest}

    @staticmethod
    def get_version_info(version_id: str) -> dict | None:
        """Look up a single version's metadata by its CDN id."""
        manifest = JSDelivrScriptureService.get_versions_manifest()
        for v in manifest:
            if v["id"] == version_id:
                return v
        return None

    @staticmethod
    def normalize_scope(raw_scope: str) -> str:
        """Normalize CDN scope values to simple categories.

        CDN values like 'Bible', 'Bible with Deuterocanon', 'Old Testament',
        'New Testament', 'New Testament+', 'Portions' → 'Full', 'NT', 'Portions'.
        """
        raw = raw_scope.lower()
        if "bible" in raw or "old testament" in raw:
            return "Full"
        if "new testament" in raw:
            return "NT"
        return "Portions"

    @staticmethod
    def _resolve_version_id(version: str) -> str:
        """Resolve a version string to a CDN version ID.

        Handles backward compatibility:
            'kjv'     → 'en-kjv'
            'en-kjv'  → 'en-kjv'
            'es-rv09' → 'es-rv09'
        """
        version = version.lower().strip()
        return JSDelivrScriptureService.SHORT_CODE_MAP.get(version, version)

    @staticmethod
    def fetch_verse(
        book: str,
        chapter: int,
        verse_start: int,
        verse_end: int | None = None,
        version: str = "kjv",
    ) -> dict:
        """Fetch verse(s) from JSDelivr CDN.

        Resolves short version codes, validates scope, and fetches from CDN.
        """
        version_id = JSDelivrScriptureService._resolve_version_id(version)

        book_slug = JSDelivrScriptureService.BOOK_SLUGS.get(book)
        if not book_slug:
            lower_to_slug = {k.lower(): v for k, v in JSDelivrScriptureService.BOOK_SLUGS.items()}
            book_slug = lower_to_slug.get(book.lower())
            if not book_slug:
                raise ValueError(f"Invalid book name: {book}")

        version_info = JSDelivrScriptureService.get_version_info(version_id)
        if version_info:
            scope = JSDelivrScriptureService.normalize_scope(version_info.get("scope", "Bible"))
            if scope == "NT" and book_slug in OLD_TESTAMENT_BOOKS:
                version_name = version_info.get("version", version_id)
                raise ValueError(
                    f"'{book}' is not available in {version_name} "
                    f"(New Testament only). Try a full Bible version like KJV."
                )

        cache_key = f"scripture:{version_id}:{book_slug}:{chapter}:{verse_start}"
        if verse_end:
            cache_key += f"-{verse_end}"

        cached = cache.get(cache_key)
        if cached:
            return cached

        if verse_end and verse_end > verse_start:
            verses = []
            structured_verses = []
            for verse_num in range(verse_start, verse_end + 1):
                verse_data = JSDelivrScriptureService._fetch_single_verse(
                    book_slug, chapter, verse_num, version_id
                )
                verses.append(verse_data["text"])
                structured_verses.append({"number": verse_num, "text": verse_data["text"]})

            verse_text = " ".join(verses)
            reference = f"{book} {chapter}:{verse_start}-{verse_end}"
        else:
            verse_data = JSDelivrScriptureService._fetch_single_verse(
                book_slug, chapter, verse_start, version_id
            )
            verse_text = verse_data["text"]
            structured_verses = [{"number": verse_start, "text": verse_text}]
            reference = f"{book} {chapter}:{verse_start}"

        display_version = version_id.upper()
        if version_info and version_info.get("localVersionAbbreviation"):
            display_version = version_info["localVersionAbbreviation"]

        result = {
            "text": verse_text,
            "verses": structured_verses,
            "reference": reference,
            "version": display_version,
            "book": book,
            "chapter": chapter,
            "verse_start": verse_start,
            "verse_end": verse_end,
        }

        cache.set(cache_key, result, 2592000)

        return result

    @staticmethod
    def _fetch_single_verse(book_slug: str, chapter: int, verse: int, version_id: str) -> dict:
        """Fetch a single verse from CDN.

        Uses the full version_id (e.g. 'en-kjv') in the URL path.
        """
        url = (
            f"{JSDelivrScriptureService.BASE_URL}/{version_id}"
            f"/books/{book_slug}/chapters/{chapter}/verses/{verse}.json"
        )

        try:
            response = requests.get(url, timeout=5)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            raise Exception(f"Failed to fetch scripture: {e!s}") from e

    @staticmethod
    def _try_fetch_verse(book_slug: str, chapter: int, verse: int, version_id: str) -> dict | None:
        """Attempt to fetch a single verse; return None on 404 / failure."""
        url = (
            f"{JSDelivrScriptureService.BASE_URL}/{version_id}"
            f"/books/{book_slug}/chapters/{chapter}/verses/{verse}.json"
        )
        try:
            response = requests.get(url, timeout=2)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            data = response.json()
            return {"number": verse, "text": data.get("text", "")}
        except requests.RequestException:
            return None

    @staticmethod
    def fetch_chapter(
        book_slug: str,
        chapter: int,
        version_id: str,
        *,
        max_verses: int = 200,
    ) -> list[dict]:
        """Fetch ALL verses in a chapter using parallel requests.

        Submits up to `max_verses` requests concurrently via ThreadPoolExecutor.
        Collects successful results and discards 404s (missing verse numbers).

        Returns a sorted list of {"number": int, "text": str} dicts.
        """
        cache_key = f"scripture:chapter:{version_id}:{book_slug}:{chapter}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        verses: list[dict] = []

        with ThreadPoolExecutor(max_workers=min(20, max_verses)) as executor:
            futures = {
                executor.submit(
                    JSDelivrScriptureService._try_fetch_verse,
                    book_slug,
                    chapter,
                    v,
                    version_id,
                ): v
                for v in range(1, max_verses + 1)
            }

            for future in as_completed(futures):
                result = future.result()
                if result is not None:
                    verses.append(result)

        verses.sort(key=lambda v: v["number"])

        if verses:
            # Cache for 24 hours (static content)
            cache.set(cache_key, verses, 86400)

        return verses

    @staticmethod
    def fetch_chapter_simple(book_slug: str, chapter: int, version_id: str) -> list[dict]:
        """Fetch ALL verses in a chapter via a single JSON request.

        Uses bibles/{version}/books/{book}/chapters/{chapter}.json.
        Timeout: 10s with 3 retries and exponential backoff.
        """
        import time

        url = f"{JSDelivrScriptureService.BASE_URL}/{version_id}/books/{book_slug}/chapters/{chapter}.json"

        for attempt in range(3):
            try:
                response = requests.get(url, timeout=10)
                response.raise_for_status()
                data = response.json().get("data", [])

                # The CDN chapter payload can carry more than one entry per
                # verse number (the verse text plus footnote/cross-reference
                # rows that reuse the same number). Keep the first entry per
                # verse so a chapter never returns doubled verses, then sort
                # for a stable, canonical order. This mirrors the DB import,
                # where the (translation, book_id, chapter, verse) unique
                # constraint already collapses these duplicates.
                seen: set[int] = set()
                verses: list[dict] = []
                for v in data:
                    try:
                        number = int(v["verse"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    if number in seen:
                        continue
                    text = (v.get("text") or "").strip()
                    if not text:
                        continue
                    seen.add(number)
                    verses.append({"number": number, "text": text})

                verses.sort(key=lambda item: item["number"])
                return verses
            except (requests.Timeout, requests.ConnectionError) as e:
                if attempt == 2:
                    logger.error(f"CDN fetch failed after 3 attempts: {e}")
                    return []
                time.sleep(2**attempt)
            except Exception as e:
                logger.error(f"CDN fetch error: {e}")
                return []
        return []

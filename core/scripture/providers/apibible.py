"""
Premium Bible scripture service using API.Bible (American Bible Society).

Supports proprietary translations like NIV and ESV with an API key.
Documentation: https://docs.api.bible

# DEACTIVATED FOR LAUNCH. See ROADMAP.md for Pro feature plan.
"""

import logging
from typing import Any

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger("core.scripture")


class APIBibleService:
    """Premium Bible service for API.Bible.

    Requires API_BIBLE_KEY in environment/settings.
    Caches the manifest for 7 days and individual verses for 30 days.
    """

    BASE_URL = "https://api.scripture.api.bible/v1"
    VERSIONS_CACHE_KEY = "scripture:apibible:versions"
    CACHE_TTL = 604800  # 7 days

    # Mappings for common versions to their API.Bible IDs
    # (Note: These are examples, actual IDs are fetched from the API)
    PREMIUM_MAP = {
        "en-niv": "de4e12af7f29f5f0-02",
        "en-esv": "592a35c71ef61374-02",
    }

    @classmethod
    def get_api_key(cls) -> str | None:
        """Returns the API key from settings."""
        return getattr(settings, "API_BIBLE_KEY", None)

    @classmethod
    def is_available(cls) -> bool:
        """Checks if the service is configured with an API key."""
        return bool(cls.get_api_key())

    @classmethod
    def get_headers(cls) -> dict:
        """Returns headers for API.Bible requests."""
        return {
            "api-key": cls.get_api_key() or "",
            "accept": "application/json",
        }

    @classmethod
    def get_versions(cls) -> list[dict[str, Any]]:
        """Fetch available Bible versions from API.Bible, with caching."""
        if not cls.is_available():
            return []

        cached = cache.get(cls.VERSIONS_CACHE_KEY)
        if cached is not None:
            return cached

        try:
            url = f"{cls.BASE_URL}/bibles"
            response = requests.get(url, headers=cls.get_headers(), timeout=10)
            response.raise_for_status()
            data = response.json().get("data", [])

            processed_versions = []
            for v in data:
                processed_versions.append(
                    {
                        "id": v["id"],
                        "version": v["name"],
                        "language": {"name": v["language"]["name"], "code": v["language"]["id"]},
                        "localVersionAbbreviation": v["abbreviation"],
                        "scope": "Bible",  # API.Bible doesn't easily expose scope in basic list
                        "provider": "apibible",
                    }
                )

            cache.set(cls.VERSIONS_CACHE_KEY, processed_versions, cls.CACHE_TTL)
            return processed_versions

        except Exception:
            logger.warning("Failed to fetch Bible versions from API.Bible", exc_info=True)
            return []

    @classmethod
    def fetch_verse(
        cls,
        book: str,
        chapter: int,
        verse_start: int,
        verse_end: int | None = None,
        version: str = "en-niv",
    ) -> dict:
        """Fetch verse(s) from API.Bible.

        API.Bible uses unique IDs for bibles (e.g. 'de4e12af7f29f5f0-01').
        We resolve our standard codes (en-niv) to these IDs.
        """
        if not cls.is_available():
            raise ValueError("API.Bible provider is not configured (API_BIBLE_KEY missing).")

        # Resolve version code if it's one of our mapped ones
        bible_id = cls.PREMIUM_MAP.get(version.lower(), version)

        # API.Bible uses 3-letter book abbreviations or IDs (e.g., JHN for John)
        # For simplicity, we assume the book ID matches our slug logic or we need a map.
        # Standard USFM IDs usually work.
        book_id = cls._get_book_id(book)

        passage_id = f"{book_id}.{chapter}.{verse_start}"
        if verse_end:
            passage_id += f"-{book_id}.{chapter}.{verse_end}"

        cache_key = f"scripture:apibible:{bible_id}:{passage_id}"
        cached = cache.get(cache_key)
        if cached:
            return cached

        url = f"{cls.BASE_URL}/bibles/{bible_id}/passages/{passage_id}"
        params = {
            "content-type": "text",
            "include-notes": "false",
            "include-titles": "false",
            "include-chapter-numbers": "false",
            "include-verse-numbers": "true",
        }

        response = requests.get(url, headers=cls.get_headers(), params=params, timeout=10)
        response.raise_for_status()
        data = response.json().get("data", {})

        verse_text = data.get("content", "").strip()
        result = {
            "text": verse_text,
            "verses": [{"number": verse_start, "text": verse_text}],
            "reference": data.get("reference", f"{book} {chapter}:{verse_start}"),
            "version": data.get("bibleId", bible_id).upper(),  # Fallback
            "book": book,
            "chapter": chapter,
            "verse_start": verse_start,
            "verse_end": verse_end,
            "provider": "apibible",
        }

        cache.set(cache_key, result, 2592000)  # 30 days
        return result

    @staticmethod
    def _get_book_id(book: str) -> str:
        """Map common book names to API.Bible 3-letter IDs."""
        mapping = {
            "Genesis": "GEN",
            "Exodus": "EXO",
            "Leviticus": "LEV",
            "Numbers": "NUM",
            "Deuteronomy": "DEU",
            "Joshua": "JOS",
            "Judges": "JDG",
            "Ruth": "RUT",
            "1 Samuel": "1SA",
            "2 Samuel": "2SA",
            "1 Kings": "1KI",
            "2 Kings": "2KI",
            "1 Chronicles": "1CH",
            "2 Chronicles": "2CH",
            "Ezra": "EZR",
            "Nehemiah": "NEH",
            "Esther": "EST",
            "Job": "JOB",
            "Psalms": "PSA",
            "Proverbs": "PRO",
            "Ecclesiastes": "ECC",
            "Song of Solomon": "SNG",
            "Isaiah": "ISA",
            "Jeremiah": "JER",
            "Lamentations": "LAM",
            "Ezekiel": "EZK",
            "Daniel": "DAN",
            "Hosea": "HOS",
            "Joel": "JOL",
            "Amos": "AMO",
            "Obadiah": "OBA",
            "Jonah": "JON",
            "Micah": "MIC",
            "Nahum": "NAM",
            "Habakkuk": "HAB",
            "Zephaniah": "ZEP",
            "Haggai": "HAG",
            "Zechariah": "ZEC",
            "Malachi": "MAL",
            "Matthew": "MAT",
            "Mark": "MRK",
            "Luke": "LUK",
            "John": "JHN",
            "Acts": "ACT",
            "Romans": "ROM",
            "1 Corinthians": "1CO",
            "2 Corinthians": "2CO",
            "Galatians": "GAL",
            "Ephesians": "EPH",
            "Philippians": "PHP",
            "Colossians": "COL",
            "1 Thessalonians": "1TH",
            "2 Thessalonians": "2TH",
            "1 Timothy": "1TI",
            "2 Timothy": "2TI",
            "Titus": "TIT",
            "Philemon": "PHM",
            "Hebrews": "HEB",
            "James": "JAS",
            "1 Peter": "1PE",
            "2 Peter": "2PE",
            "1 John": "1JN",
            "2 John": "2JN",
            "3 John": "3JN",
            "Jude": "JUD",
            "Revelation": "REV",
        }
        return mapping.get(book, book[:3].upper())

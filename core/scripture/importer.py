"""
ScriptureImporter Service

A production-grade, fault-tolerant data pipeline for importing
Bible translations into Ziona's PostgreSQL database.
"""

import logging
import time
from typing import Any

import requests
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from core.scripture.constants import BOOK_ID_MAP, BOOK_SLUG_TO_ID
from core.scripture.models import BibleTranslation, BibleVerse, ScriptureBook
from core.scripture.providers.jsdelivr import JSDelivrScriptureService

logger = logging.getLogger("core.scripture")


class ScriptureImporter:
    """Refactored isolated import pipeline service."""

    JSDELIVR_BASE = "https://cdn.jsdelivr.net/gh/wldeh/bible-api/bibles"

    # Map for books that use different slugs across different CDN versions
    ALT_SLUGS = {
        "songofsolomon": ["songofsongs"],
        "1samuel": ["1-samuel"],  # Added for extra safety
        "2samuel": ["2-samuel"],
    }

    # Safety: Kill the import if it runs longer than 1 hour to prevent hanging Celery workers
    MAX_RUNTIME_SECONDS = 3600

    def __init__(self, batch_size: int = 1000, resume: bool = False):
        self.batch_size = batch_size
        self.resume = resume

        # Ensure optimal TCP connection pooling for CDN fetch
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Ziona-Scripture-Importer/2.0 (Production)",
                "Accept": "application/json",
            }
        )

    def run_import(self, translations: list[str]) -> None:
        """Entry point to process a list of translations."""
        # 1. Master Kill Switch (Senior Guard for emergency control)
        if getattr(settings, "DISABLE_SCRIPTURE_IMPORT", False):
            logger.warning("Scripture import is DISABLED via settings. Skipping.")
            return

        # 2. Global Concurrency Lock (Prevents 'Thundering Herd' on server restarts)
        lock_key = "lock:scripture_import_active"
        if cache.get(lock_key):
            logger.warning(
                "Another scripture import job is already active. Skipping to avoid collision."
            )
            return

        # Lock for 1 hour (matches MAX_RUNTIME_SECONDS)
        cache.set(lock_key, True, timeout=self.MAX_RUNTIME_SECONDS)

        try:
            # Seeding books is safe/idempotent
            self._seed_books()

            start_time = time.time()
            for code in translations:
                # Global Timeout Check
                if time.time() - start_time > self.MAX_RUNTIME_SECONDS:
                    logger.error(
                        f"Global runtime limit reached ({self.MAX_RUNTIME_SECONDS}s). Aborting to prevent hang."
                    )
                    break

                translation_code = code.lower()
                if self.resume and self._is_translation_fully_imported(translation_code):
                    logger.info(f"Skipping {translation_code} (already complete).")
                    continue

                self._import_translation(translation_code)

        finally:
            self.session.close()
            cache.delete(lock_key)

    def _is_translation_fully_imported(self, translation_code: str) -> bool:
        """Check if a translation has sufficient verses to be considered fully imported."""
        translation = BibleTranslation.objects.filter(code=translation_code).first()
        # A typical full Bible has ~31k verses.
        return bool(translation and translation.verse_count > 30000)

    def _seed_books(self) -> None:
        """Seed ScriptureBook table with canonical IDs 1-66 and chapter counts."""
        from core.scripture.services import ScriptureService

        # Get canonical counts directly from our service's master dictionary
        books_list = ScriptureService.get_books_list()
        slug_to_chapters = {b["slug"]: b["chapters"] for b in books_list}

        books_to_create = []
        for book_slug, book_id in BOOK_SLUG_TO_ID.items():
            book_name = BOOK_ID_MAP.get(book_id)
            if not book_name:
                continue

            testament = "OT" if book_id <= 39 else "NT"
            chapters = slug_to_chapters.get(book_slug, 0)

            books_to_create.append(
                ScriptureBook(
                    id=book_id,
                    name=book_name,
                    slug=book_slug,
                    testament=testament,
                    chapters=chapters,
                )
            )

        # Use update_conflicts on PostgreSQL to ensure existing books get the chapter counts injected
        ScriptureBook.objects.bulk_create(
            books_to_create,
            update_conflicts=True,
            update_fields=["chapters", "slug", "name"],
            unique_fields=["id"],
        )
        logger.info("Seeded canonical ScriptureBooks structure with enforced chapter boundaries.")

    def _import_translation(self, translation_code: str) -> None:
        """Import a single translation book by book, chapter by chapter."""
        version_id = JSDelivrScriptureService._resolve_version_id(translation_code)

        # Initialize or update translation metadata (is_active=True allows immediate staging use)
        trans_obj, _ = BibleTranslation.objects.update_or_create(
            code=translation_code,
            defaults={
                "name": f"{translation_code.upper()} Translation",
                "source": "jsdelivr",
                "is_active": True,
            },
        )

        books = ScriptureBook.objects.all().order_by("id")

        for book in books:
            # Try primary slug, fallback to alt if Chapter 1 is missing
            self._import_book_with_fallback(trans_obj, book, version_id)

        # Final metadata rollup
        self._update_translation_metadata(trans_obj)

    def _import_book_with_fallback(
        self, trans_obj: BibleTranslation, book: ScriptureBook, version_id: str
    ) -> None:
        """Attempt import with primary slug, then try alts if Chapter 1 is missing."""
        slugs_to_try = [book.slug] + self.ALT_SLUGS.get(book.slug, [])

        for slug in slugs_to_try:
            if self._import_book(trans_obj, book, version_id, override_slug=slug):
                return  # Success!

        logger.warning(
            f"Failed to import {book.name} ({trans_obj.code}) with any known slugs: {slugs_to_try}"
        )

    def _import_book(
        self,
        trans_obj: BibleTranslation,
        book: ScriptureBook,
        version_id: str,
        override_slug: str = None,
    ) -> bool:
        """Iterate through chapters of a book, importing data per-chapter.

        Returns True if any data was found, False if the slug was invalid (404 on Ch 1).
        """
        chapter = 1
        book_slug = override_slug or book.slug
        any_data_found = False

        # If resuming, discover the last successfully imported chapter.
        if self.resume:
            existing_chapters = (
                BibleVerse.objects.filter(translation=trans_obj.code, book_id=book.id)
                .values_list("chapter", flat=True)
                .distinct()
                .order_by("-chapter")
            )

            if existing_chapters:
                latest = existing_chapters[0]
                chapter = max(1, latest)
                any_data_found = True

                # Delete the latest partially completed chapter cleanly before pulling again
                BibleVerse.objects.filter(
                    translation=trans_obj.code, book_id=book.id, chapter=chapter
                ).delete()

        while True:
            # Enforce canonical boundary constraint
            if book.chapters > 0 and chapter > book.chapters:
                logger.info(f"Reached canonical end of {book.name} (Chapter {book.chapters}).")
                break

            # 1. Fetch
            url = f"{self.JSDELIVR_BASE}/{version_id}/books/{book_slug}/chapters/{chapter}.json"
            start_time = time.time()
            verses_data = self._fetch_chapter_data(url)
            duration_ms = int((time.time() - start_time) * 1000)

            if verses_data is None:
                # 404 or 403 (Handled in _fetch_chapter_data) indicates end of book
                break

            any_data_found = True

            # 2. Parse (Data Integrity enforcement)
            verses_to_create = self._parse_verses(verses_data, trans_obj.code, book, chapter)

            # 3. Save (Atomic limit)
            if verses_to_create:
                self._save_verses_bulk(verses_to_create)

            logger.info(
                "chapter_imported",
                extra={
                    "translation": trans_obj.code,
                    "book": book.name,
                    "chapter": chapter,
                    "verses_imported": len(verses_to_create),
                    "duration_ms": duration_ms,
                },
            )

            # 🛡️ Pacing (Senior Guard): 100ms breather for Free Tier stability
            # Prevents GitHub/JSDelivr banning and Rent CPU spikes
            time.sleep(0.1)
            chapter += 1

        # Avoid repeated count queries. Update book chapter count efficiently at the end of the book.
        if chapter > 1:
            book.chapters = chapter - 1
            book.save(update_fields=["chapters"])

        return any_data_found

    def _fetch_chapter_data(self, url: str, max_retries: int = 5) -> list[dict[str, Any]] | None:
        """Fetch CDN URL with robust exponential backoff.

        Handles timeouts, 5xx server errors, and 429 rate limits.
        """
        for attempt in range(max_retries):
            try:
                response = self.session.get(url, timeout=30)

                if response.status_code in (404, 403):
                    # We accept both 404 and 403 as end-of-book guard rails
                    # because JSDelivr CDN unpredictably issues 403s on missing edge-cached folders
                    return None

                if response.status_code == 429:
                    logger.warning(f"Rate limited by CDN. Retrying {url}")
                    time.sleep(2 ** (attempt + 2))
                    continue

                if response.status_code >= 500:
                    logger.warning(f"CDN 5xx error at {url}")
                    time.sleep(2**attempt)
                    continue

                if response.status_code == 400:
                    raise ValueError(
                        f"Unexpected status {response.status_code} for {url}. CDN blocking?"
                    )

                response.raise_for_status()
                data = response.json()
                return data.get("data", [])

            except (requests.Timeout, requests.ConnectionError) as e:
                logger.warning(
                    f"Network error fetching {url} (attempt {attempt + 1}/{max_retries}): {e}"
                )
                if attempt == max_retries - 1:
                    logger.error(f"Fatal error fetching {url} after {max_retries} attempts.")
                    raise
                time.sleep(2**attempt)
            except Exception as e:
                # E.g. ValueError explicitly raised above
                if "Unexpected status" in str(e):
                    raise
                logger.exception(f"Unexpected payload fetching {url}")
                return None

        raise RuntimeError(f"Max retries exceeded for {url}")

    def _parse_verses(
        self,
        verses_data: list[dict[str, Any]],
        translation_code: str,
        book: ScriptureBook,
        chapter: int,
    ) -> list[BibleVerse]:
        """Parse raw network payload into BibleVerse ORM instances."""
        verses_to_create = []
        for v in verses_data:
            try:
                # 🛡️ Data Validation Guard
                verse_text = v.get("text", "").strip()
                if not verse_text:
                    logger.warning(f"Skipping empty verse in {book.name} {chapter}")
                    continue

                verse_num = int(v["verse"])
                verses_to_create.append(
                    BibleVerse(
                        translation=translation_code,
                        book_id=book.id,
                        chapter=chapter,
                        verse=verse_num,
                        text=verse_text,
                        book_name=book.name,
                    )
                )
            except (ValueError, KeyError) as e:
                logger.error(f"Skipping malformed verse in {book.name} {chapter}: {e}")
        return verses_to_create

    def _save_verses_bulk(self, verses: list[BibleVerse]) -> None:
        """Atomic batch insert using bulk_create and conflict ignoring.

        Per-chapter transaction isolated so failures don't blast the entire book's progress.
        """
        with transaction.atomic():
            BibleVerse.objects.bulk_create(
                verses, batch_size=self.batch_size, ignore_conflicts=True
            )

    def _update_translation_metadata(self, trans_obj: BibleTranslation) -> None:
        """Update rollup verse_count entirely inside Postgres using ORM aggregrate."""
        count = BibleVerse.objects.filter(translation=trans_obj.code).count()
        trans_obj.verse_count = count
        trans_obj.imported_at = timezone.now()
        trans_obj.save(update_fields=["verse_count", "imported_at"])

        logger.info(
            "translation_completed", extra={"translation": trans_obj.code, "total_verses": count}
        )

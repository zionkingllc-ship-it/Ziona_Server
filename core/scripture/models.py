"""
Scripture database models for high-performance Bible verse storage.

Provides PostgreSQL-backed storage for Bible translations, replacing
the JSDelivr CDN as the primary data source while keeping CDN as fallback.
"""

from django.db import models


class ScriptureBook(models.Model):
    """Canonical Bible book with integer ID for fast queries.

    Uses explicit integer IDs (1-66) matching standard biblical ordering.
    Named ScriptureBook (not BibleBook) to avoid collision with the
    existing Strawberry GraphQL BibleBook type in schema.py.
    """

    id = models.IntegerField(primary_key=True)  # 1-66
    name = models.CharField(max_length=50, unique=True)  # "Genesis"
    slug = models.SlugField(max_length=50, unique=True)  # "genesis"
    testament = models.CharField(
        max_length=2,
        choices=[("OT", "Old Testament"), ("NT", "New Testament")],
    )
    chapters = models.IntegerField(default=0)  # Total chapter count

    class Meta:
        db_table = "scripture_books"
        ordering = ["id"]

    def __str__(self):
        return self.name


class BibleVerse(models.Model):
    """Indexed Bible verse for sub-50ms retrieval.

    Denormalizes book_name to avoid joins on the hot read path.
    All translations stored lowercase (kjv, asv, web, rv) to match
    existing FREE_BIBLE_VERSIONS and CDN URL conventions.
    """

    translation = models.CharField(max_length=20, db_index=True)  # kjv, asv, web, rv
    book_id = models.IntegerField(db_index=True)  # 1-66 (FK to ScriptureBook)
    chapter = models.IntegerField(db_index=True)
    verse = models.IntegerField(db_index=True)
    text = models.TextField()

    # Denormalized for query performance — avoids JOIN on every read
    book_name = models.CharField(max_length=50)

    class Meta:
        db_table = "scripture_verses"
        indexes = [
            # Primary query pattern: fetch_chapter(translation, book, chapter)
            models.Index(
                fields=["translation", "book_id", "chapter", "verse"],
                name="idx_verse_primary_lookup",
            ),
            # Chapter queries (most common from GraphQL)
            models.Index(
                fields=["book_id", "chapter"],
                name="idx_verse_book_chapter",
            ),
            # Translation browsing
            models.Index(
                fields=["translation", "book_id"],
                name="idx_verse_trans_book",
            ),
        ]
        unique_together = ["translation", "book_id", "chapter", "verse"]

    def __str__(self):
        return f"{self.book_name} {self.chapter}:{self.verse} ({self.translation})"


class BibleTranslation(models.Model):
    """Supported Bible translation metadata.

    Tracks which translations are imported and available.
    Code stored lowercase (kjv, asv, web, rv).
    """

    code = models.CharField(max_length=20, primary_key=True)  # kjv
    name = models.CharField(max_length=200)  # King James Version
    language = models.CharField(max_length=10, default="en")
    source = models.CharField(max_length=20, default="jsdelivr")
    is_active = models.BooleanField(default=True)
    verse_count = models.IntegerField(default=0)  # Track import completeness
    imported_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "scripture_translations"

    def __str__(self):
        return f"{self.code.upper()} — {self.name}"

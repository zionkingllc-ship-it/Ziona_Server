"""GraphQL schema specific to scripture metadata lookup."""


import strawberry

from core.scripture.services import ScriptureError, ScriptureService


@strawberry.type
class BibleVersion:
    code: str
    name: str
    abbreviation: str
    language: str
    scope: str
    free: bool


@strawberry.type
class BibleBook:
    name: str
    slug: str
    chapters: int


@strawberry.type
class ScriptureResult:
    text: str
    reference: str
    version: str
    book: str
    chapter: int
    verseStart: int
    verseEnd: int | None = None


@strawberry.type
class ScriptureQueries:
    @strawberry.field
    def bibleVersions(self, language: str | None = None) -> list[BibleVersion]:
        """Get available Bible versions, optionally filtered by language."""
        versions = ScriptureService.get_available_versions()

        if language:
            language_lower = language.lower()
            versions = [v for v in versions if v["language"].lower() == language_lower]

        return [
            BibleVersion(
                code=v["code"],
                name=v["name"],
                abbreviation=v["abbreviation"],
                language=v["language"],
                scope=v["scope"],
                free=v["free"],
            )
            for v in versions
        ]

    @strawberry.field
    def bibleBooks(self, testament: str = "all") -> list[BibleBook]:
        """Get list of Bible books mapping."""
        books = ScriptureService.get_books_list(testament=testament)
        return [
            BibleBook(
                name=b["name"],
                slug=b["slug"],
                chapters=b["chapters"],
            )
            for b in books
        ]

    @strawberry.field
    def scripture(
        self,
        book: str,
        chapter: int,
        verseStart: int,
        verseEnd: int | None = None,
        version: str = "kjv",
    ) -> ScriptureResult | None:
        """Fetch specific verse (for preview)."""
        try:
            result = ScriptureService.fetch_verse(
                book=book,
                chapter=chapter,
                verse_start=verseStart,
                verse_end=verseEnd,
                version=version,
            )
            return ScriptureResult(
                text=result["text"],
                reference=result["reference"],
                version=result["version"],
                book=result["book"],
                chapter=result["chapter"],
                verseStart=result["verse_start"],
                verseEnd=result["verse_end"],
            )
        except ScriptureError:
            return None

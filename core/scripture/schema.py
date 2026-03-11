"""GraphQL schema specific to scripture metadata lookup."""


import strawberry

from core.scripture.services import ScriptureError, ScriptureService


@strawberry.type
class BibleVersion:
    """
    Metadata representation of an available Bible Translation natively.
    """

    code: str = strawberry.field(description="Short code ('kjv')")
    name: str = strawberry.field(description="Full text name ('King James Version')")
    abbreviation: str = strawberry.field(description="Display tag ('KJV')")
    language: str = strawberry.field(description="ISO Code ('eng')")
    scope: str = strawberry.field(description="Coverage bounds ('complete', 'nt')")
    free: bool = strawberry.field(description="Public domain flag")


@strawberry.type
class BibleBook:
    """
    Lookup metadata bounds for a particular canonical book mapping.
    """

    name: str = strawberry.field(description="Full label ('Genesis')")
    slug: str = strawberry.field(description="URL safe ('genesis')")
    chapters: int = strawberry.field(description="Volume capacity Cap")


@strawberry.type
class ScriptureResult:
    """
    Explicit verse text payload resulting from exact coordinates mapping.

    **Authentication:** Optional depending on query globally
    **Related operations:** scripture
    """

    text: str = strawberry.field(description="The canonical sequence itself")
    reference: str = strawberry.field(description="Formatted label string literal ('John 3:16')")
    version: str = strawberry.field(description="Translation metadata")
    book: str = strawberry.field(description="Root index mapping")
    chapter: int = strawberry.field(description="Coordinates")
    verseStart: int = strawberry.field(description="Coordinates")
    verseEnd: int | None = strawberry.field(
        default=None, description="Coordinates bounded natively"
    )


@strawberry.type
class ScriptureQueries:
    @strawberry.field(
        description="Extract canonical list representing available supported translations."
    )
    def bibleVersions(self, language: str | None = None) -> list[BibleVersion]:
        """
        Get bounded array list mapping metadata for Scripture engine versions natively valid.

        **Authentication:** Not required
        **Parameters:**
        - language (String, optional) - Bounding implicitly
        **Returns:** Array list mapping available dictionaries
        **Errors:** Fails safely natively empty struct bounds.
        """
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

    @strawberry.field(description="Filter hierarchical mapping structure list of volumes cleanly.")
    def bibleBooks(self, testament: str = "all") -> list[BibleBook]:
        """
        Fetch valid indexing constants metadata supporting verse picking organically.

        **Authentication:** Not required
        **Parameters:**
        - testament (String, optional) - Section bounds explicitly
        **Returns:** Array sequence BibleBook dictionaries mapped seamlessly
        **Errors:** Bounded implicitly.
        """
        books = ScriptureService.get_books_list(testament=testament)
        return [
            BibleBook(
                name=b["name"],
                slug=b["slug"],
                chapters=b["chapters"],
            )
            for b in books
        ]

    @strawberry.field(
        description="Query mapping sequence directly returning bounded text preview literal."
    )
    def scripture(
        self,
        book: str,
        chapter: int,
        verseStart: int,
        verseEnd: int | None = None,
        version: str = "kjv",
    ) -> ScriptureResult | None:
        """
        Fetch explicit specific coordinates safely previewing verse text literal mapped natively.

        **Authentication:** Not required
        **Parameters:**
        - book (String, required) - Volume
        - chapter (Int, required) - Area
        - verseStart (Int, required) - Bounds
        - verseEnd (Int, optional) - Expanded bounds
        - version (String, optional) - Translation lookup
        **Returns:** Nullable ScriptureResult text payload explicitly
        **Errors:** Fails yielding None seamlessly avoiding throws mapping cleanly.
        """
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

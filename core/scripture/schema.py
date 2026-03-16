import strawberry
from graphql import GraphQLError

from core.scripture.constants import FREE_BIBLE_VERSIONS
from core.scripture.exceptions import VersionNotAvailableError
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
class VerseType:
    """A single Bible verse."""

    number: int
    text: str


@strawberry.type
class ScriptureType:
    """
    Explicit verse text payload resulting from exact coordinates mapping.
    """

    verses: list[VerseType]
    text: str = strawberry.field(description="The full canonical sequence")
    reference: str = strawberry.field(description="Formatted label string literal ('John 3:16')")
    version: str = strawberry.field(description="Translation metadata")
    book: str = strawberry.field(description="Root index mapping")
    chapter: int = strawberry.field(description="Coordinates")
    verse_start: int = strawberry.field(description="Coordinates")
    verse_end: int | None = strawberry.field(
        default=None, description="Coordinates bounded natively"
    )


@strawberry.type
class ScriptureQueries:
    @strawberry.field(
        description="Extract canonical list representing available supported free translations."
    )
    def bibleVersions(self, language: str | None = None) -> list[BibleVersion]:
        """
        Get bounded array list mapping metadata for Scripture engine versions natively valid.
        Currently limited to free tier versions for launch (KJV, ASV, WEB, RV).

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
        description="Fetch explicit specific coordinates safely previewing verse text literal mapped natively. "
        "Only free-tier versions (kjv, asv, web, rv) are supported for launch."
    )
    def scripture(
        self,
        book: str,
        chapter: int,
        verseStart: int,
        verseEnd: int | None = None,
        version: str = "kjv",
    ) -> ScriptureType | None:
        """
        Fetch explicit specific coordinates safely previewing verse text literal mapped natively.

        **Authentication:** Not required
        **Parameters:**
        - book (String, required) - Volume
        - chapter (Int, required) - Area
        - verseStart (Int, required) - Bounds
        - verseEnd (Int, optional) - Expanded bounds
        - version (String, optional) - Translation lookup (kjv, asv, web, rv)
        **Returns:** Nullable ScriptureType text payload explicitly
        **Errors:** Fails yielding None if version is restricted or fetch fails.
        """
        try:
            result = ScriptureService.fetch_verse(
                book=book,
                chapter=chapter,
                verse_start=verseStart,
                verse_end=verseEnd,
                version=version,
            )
            return ScriptureType(
                text=result["text"],
                verses=[VerseType(number=v["number"], text=v["text"]) for v in result["verses"]],
                reference=result["reference"],
                version=result["version"],
                book=result["book"],
                chapter=result["chapter"],
                verse_start=result["verse_start"],
                verse_end=result["verse_end"],
            )
        except VersionNotAvailableError as e:
            raise GraphQLError(
                str(e),
                extensions={
                    "code": "VERSION_NOT_AVAILABLE",
                    "availableVersions": FREE_BIBLE_VERSIONS,
                },
            ) from e
        except ScriptureError:
            return None

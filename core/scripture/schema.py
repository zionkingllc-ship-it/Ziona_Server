import strawberry
from graphql import GraphQLError

from core.scripture.constants import FREE_BIBLE_VERSIONS
from core.scripture.exceptions import ScriptureError, VersionNotAvailableError
from core.scripture.services import ScriptureService
from core.shared.types import ScriptureVerse


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
class ScriptureResponse:
    """Full chapter response containing all verses."""

    book: str = strawberry.field(description="Book name ('John')")
    chapter: int = strawberry.field(description="Chapter number")
    translation: str = strawberry.field(description="Translation version ('kjv')")
    verses: list[ScriptureVerse] = strawberry.field(description="All verses in the chapter")


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
        description="Fetch all verses in a chapter. Returns a ScriptureResponse "
        "with book, chapter, version, and every verse in the chapter."
    )
    def scripture(
        self,
        book: str,
        chapter: int,
        translation: str = "kjv",
    ) -> ScriptureResponse:
        """
        Fetch all verses in a specific chapter.

        **Authentication:** Not required
        **Parameters:**
        - book (String!, required) — Book name (e.g. "John")
        - chapter (Int!, required) — Chapter number
        - version (String!, optional, default "kjv") — Bible translation
        **Returns:** ScriptureResponse with all verses
        **Errors:**
        - INVALID_BOOK — book not found
        - INVALID_CHAPTER — chapter exceeds book's chapters
        - CHAPTER_NOT_FOUND — no verses returned from CDN
        - VERSION_NOT_AVAILABLE — version not in free tier
        """
        try:
            verses = ScriptureService.fetch_chapter(book=book, chapter=chapter, version=translation)
            return ScriptureResponse(
                book=book,
                chapter=chapter,
                translation=translation.lower().strip(),
                verses=[ScriptureVerse(number=v["number"], text=v["text"]) for v in verses],
            )
        except VersionNotAvailableError as e:
            raise GraphQLError(
                str(e),
                extensions={
                    "code": "VERSION_NOT_AVAILABLE",
                    "availableVersions": FREE_BIBLE_VERSIONS,
                },
            ) from e
        except ScriptureError as e:
            raise GraphQLError(
                str(e),
                extensions={"code": e.code},
            ) from e

    @strawberry.field(
        description="Fetch verses in a range and return combined text as a single string. "
        "No verse numbers included — just the concatenated text."
    )
    def scriptureRange(
        self,
        book: str,
        chapter: int,
        translation: str = "kjv",
        verseStart: int = 1,
        verseEnd: int | None = None,
    ) -> str:
        """
        Fetch a verse range and return the combined text.

        **Authentication:** Not required
        **Parameters:**
        - book (String!, required) — Book name
        - chapter (Int!, required) — Chapter number
        - version (String!, optional, default "kjv") — Translation
        - verseStart (Int!, required) — First verse number
        - verseEnd (Int, optional) — Last verse number (defaults to verseStart)
        **Returns:** Combined verse text (String!)
        **Errors:**
        - INVALID_BOOK — book not found
        - VERSE_RANGE_INVALID — verseEnd < verseStart
        - VERSION_NOT_AVAILABLE — version not in free tier
        """
        # Validate verse range
        if verseEnd is not None and verseEnd < verseStart:
            raise GraphQLError(
                f"verseEnd ({verseEnd}) must be >= verseStart ({verseStart})",
                extensions={"code": "VERSE_RANGE_INVALID"},
            )

        try:
            result = ScriptureService.fetch_verse(
                book=book,
                chapter=chapter,
                verse_start=verseStart,
                verse_end=verseEnd,
                version=translation,
            )
            return result["text"]
        except VersionNotAvailableError as e:
            raise GraphQLError(
                str(e),
                extensions={
                    "code": "VERSION_NOT_AVAILABLE",
                    "availableVersions": FREE_BIBLE_VERSIONS,
                },
            ) from e
        except ScriptureError as e:
            raise GraphQLError(
                str(e),
                extensions={"code": e.code},
            ) from e

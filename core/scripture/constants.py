"""
Scripture constants for Ziona Bible Service.
Defines supported free and premium versions.
"""

# Free versions available at launch (via JSDelivr CDN)
FREE_BIBLE_VERSIONS = [
    "kjv",  # King James Version
    "asv",  # American Standard Version
    "rv",  # Revised Version 1885
    "web",  # World English Bible
]

# Premium versions for future Pro features (via API.Bible or other sources)
PREMIUM_BIBLE_VERSIONS = [
    "niv",  # New International Version
    "esv",  # English Standard Version
    "nlt",  # New Living Translation
    "nasb",  # New American Standard Bible
]

# JSDelivr API returns long names, map to abbreviations for mobile dropdowns
TRANSLATION_MAPPING = {
    # King James versions
    "King James Version [eng] without Strong's numbers, 1769 standardized text": "KJV",
    "King James Version": "KJV",
    "Thai KJV": "Thai KJV",
    # Revised versions
    "Revised Version 1885": "RV1885",
    "Revised Version": "RV1885",
    # World English Bible
    "World English Bible (American Edition)": "WEB",
    "World English Bible": "WEB",
    # American Standard
    "American Standard Version of 1901 [eng] ASV": "ASV",
    "American Standard Version": "ASV",
    # New International Version
    "New International Version": "NIV",
    # English Standard Version
    "English Standard Version": "ESV",
    # New Living Translation
    "New Living Translation": "NLT",
    # New American Standard
    "New American Standard Bible": "NASB",
}


def normalize_translation(raw_translation: str) -> str:
    """
    Convert JSDelivr's verbose translation names to mobile-friendly abbreviations.

    Args:
        raw_translation: Long translation name from JSDelivr API

    Returns:
        Abbreviated translation name (e.g., "KJV")
    """
    if not raw_translation:
        return "KJV"

    # Return mapped abbreviation, or original if not in mapping
    # Also handle already short codes like 'kjv' -> 'KJV'
    if raw_translation.upper() in ["KJV", "ASV", "RV", "WEB", "RV1885", "NIV", "ESV"]:
        return raw_translation.upper()

    return TRANSLATION_MAPPING.get(raw_translation, raw_translation)


def get_translation_id(abbreviation: str) -> str:
    """
    Convert abbreviation back to JSDelivr API ID for fetching natively gracefully.

    Args:
        abbreviation: Short name like "KJV"

    Returns:
        Safe internal short code like "kjv" compatible with JSDelivrScriptureService
    """
    if not abbreviation:
        return "kjv"

    abbr_upper = abbreviation.upper()

    # Map back to internal short codes acceptable by _resolve_version_id natively
    reverse_codes = {
        "KJV": "kjv",
        "ASV": "asv",
        "WEB": "web",
        "RV1885": "rv",
        "RV": "rv",
    }

    if abbr_upper in reverse_codes:
        return reverse_codes[abbr_upper]

    for _, abbrev in TRANSLATION_MAPPING.items():
        if abbrev == abbreviation:
            return abbreviation.lower()

    # Pass through unknown versions (lowered) so downstream validation can reject them
    return abbreviation.lower().strip()


# ── Book ID Mappings (1-66) ──────────────────────────────────────────

BOOK_ID_MAP = {
    # Old Testament (1-39)
    1: "Genesis",
    2: "Exodus",
    3: "Leviticus",
    4: "Numbers",
    5: "Deuteronomy",
    6: "Joshua",
    7: "Judges",
    8: "Ruth",
    9: "1 Samuel",
    10: "2 Samuel",
    11: "1 Kings",
    12: "2 Kings",
    13: "1 Chronicles",
    14: "2 Chronicles",
    15: "Ezra",
    16: "Nehemiah",
    17: "Esther",
    18: "Job",
    19: "Psalms",
    20: "Proverbs",
    21: "Ecclesiastes",
    22: "Song of Solomon",
    23: "Isaiah",
    24: "Jeremiah",
    25: "Lamentations",
    26: "Ezekiel",
    27: "Daniel",
    28: "Hosea",
    29: "Joel",
    30: "Amos",
    31: "Obadiah",
    32: "Jonah",
    33: "Micah",
    34: "Nahum",
    35: "Habakkuk",
    36: "Zephaniah",
    37: "Haggai",
    38: "Zechariah",
    39: "Malachi",
    # New Testament (40-66)
    40: "Matthew",
    41: "Mark",
    42: "Luke",
    43: "John",
    44: "Acts",
    45: "Romans",
    46: "1 Corinthians",
    47: "2 Corinthians",
    48: "Galatians",
    49: "Ephesians",
    50: "Philippians",
    51: "Colossians",
    52: "1 Thessalonians",
    53: "2 Thessalonians",
    54: "1 Timothy",
    55: "2 Timothy",
    56: "Titus",
    57: "Philemon",
    58: "Hebrews",
    59: "James",
    60: "1 Peter",
    61: "2 Peter",
    62: "1 John",
    63: "2 John",
    64: "3 John",
    65: "Jude",
    66: "Revelation",
}

# Name → Book ID (reverse lookup)
BOOK_NAME_TO_ID = {name: book_id for book_id, name in BOOK_ID_MAP.items()}

# Slug → Book ID (uses hyphenated format matching CDN URLs and BOOK_SLUGS in jsdelivr.py)
BOOK_SLUG_TO_ID = {
    "genesis": 1,
    "exodus": 2,
    "leviticus": 3,
    "numbers": 4,
    "deuteronomy": 5,
    "joshua": 6,
    "judges": 7,
    "ruth": 8,
    "1samuel": 9,
    "2samuel": 10,
    "1kings": 11,
    "2kings": 12,
    "1chronicles": 13,
    "2chronicles": 14,
    "ezra": 15,
    "nehemiah": 16,
    "esther": 17,
    "job": 18,
    "psalms": 19,
    "proverbs": 20,
    "ecclesiastes": 21,
    "songofsolomon": 22,
    "isaiah": 23,
    "jeremiah": 24,
    "lamentations": 25,
    "ezekiel": 26,
    "daniel": 27,
    "hosea": 28,
    "joel": 29,
    "amos": 30,
    "obadiah": 31,
    "jonah": 32,
    "micah": 33,
    "nahum": 34,
    "habakkuk": 35,
    "zephaniah": 36,
    "haggai": 37,
    "zechariah": 38,
    "malachi": 39,
    "matthew": 40,
    "mark": 41,
    "luke": 42,
    "john": 43,
    "acts": 44,
    "romans": 45,
    "1corinthians": 46,
    "2corinthians": 47,
    "galatians": 48,
    "ephesians": 49,
    "philippians": 50,
    "colossians": 51,
    "1thessalonians": 52,
    "2thessalonians": 53,
    "1timothy": 54,
    "2timothy": 55,
    "titus": 56,
    "philemon": 57,
    "hebrews": 58,
    "james": 59,
    "1peter": 60,
    "2peter": 61,
    "1john": 62,
    "2john": 63,
    "3john": 64,
    "jude": 65,
    "revelation": 66,
}

# Book ID → Slug (for CDN URL construction)
BOOK_ID_TO_SLUG = {book_id: slug for slug, book_id in BOOK_SLUG_TO_ID.items()}


def get_book_id(book_name: str) -> int | None:
    """Resolve a book name, slug, or alias to its canonical integer ID.

    Tries exact name match first, then slug match (case-insensitive).

    Args:
        book_name: Book name ("Genesis"), slug ("genesis"), or alias ("Psalm").

    Returns:
        Integer book ID (1-66) or None if not found.
    """
    # Try exact name match
    if book_name in BOOK_NAME_TO_ID:
        return BOOK_NAME_TO_ID[book_name]

    # Try slug (case-insensitive, preserving hyphens)
    slug = book_name.lower().strip()
    if slug in BOOK_SLUG_TO_ID:
        return BOOK_SLUG_TO_ID[slug]

    # Handle common aliases
    aliases = {
        "psalm": 19,  # Psalm → Psalms
        "song of songs": 22,  # Song of Songs → Song of Solomon
    }
    return aliases.get(slug)


def get_book_name(book_id: int) -> str | None:
    """Get canonical book name from integer ID.

    Args:
        book_id: Integer book ID (1-66).

    Returns:
        Book name string or None if invalid.
    """
    return BOOK_ID_MAP.get(book_id)

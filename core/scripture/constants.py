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

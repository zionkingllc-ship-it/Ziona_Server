"""
Core shared utilities for Ziona Server.

Provides common helper functions used across all modules.
"""

import logging

logger = logging.getLogger("core.shared")


def normalize_url(url: str) -> str:
    """Fix doubled https:// prefixes from storage URL generation.

    Prevents the bug where GCS fallback URLs get constructed as:
        https://storage.googleapis.com/.../https://storage.googleapis.com/...

    Args:
        url: The URL string to normalize.

    Returns:
        The corrected URL with at most one https:// prefix.
    """
    if not url:
        return url

    if url.count("https://") > 1:
        # Keep only the last valid https:// segment
        parts = url.split("https://")
        return "https://" + parts[-1]

    return url

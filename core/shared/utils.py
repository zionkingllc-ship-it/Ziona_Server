"""
Core shared utilities for Ziona Server.

Provides common helper functions used across all modules.
"""

import logging
import math
from urllib.parse import quote

logger = logging.getLogger("core.shared")


def normalize_duration_seconds(duration: int | float | None) -> int | None:
    """Normalize precise media duration to whole API seconds.

    Media processing retains fractional seconds for validation and diagnostics,
    while the public GraphQL contract exposes duration as an integer. Rounding
    up avoids understating playback length (for example, 6.83 becomes 7).
    """
    if duration is None:
        return None

    try:
        value = float(duration)
    except (TypeError, ValueError) as exc:
        raise ValueError("Media duration must be numeric") from exc

    if not math.isfinite(value) or value < 0:
        raise ValueError("Media duration must be a finite, non-negative number")

    return math.ceil(value)


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


def build_post_share_url(base_url: str, post_id: str) -> str:
    """Build the canonical public share URL for a post.

    Args:
        base_url: App share base URL, e.g. "https://ziona.app".
        post_id: Post UUID/string identifier.

    Returns:
        Canonical post share URL.
    """
    normalized_base = (base_url or "https://ziona.app").rstrip("/")
    return f"{normalized_base}/post/{quote(str(post_id), safe='')}"


def format_count(count: int) -> str:
    """Format large numbers for display.

    Precision rules:
        - 999 -> "999"
        - 1000 -> "1k"
        - 1500 -> "1.5k"
        - 10000 -> "10k"
        - Trailing zeros are stripped: "1.0k" -> "1k"

    Args:
        count: The integer to format.

    Returns:
        A human-readable string representation of the count.
    """
    if not isinstance(count, int | float):
        try:
            count = int(count)
        except (ValueError, TypeError):
            return "0"

    if count < 1000:
        return str(int(count))

    if count < 1_000_000:
        # Thousands
        k = count / 1000
        if k == int(k):
            return f"{int(k)}k"
        # Format to 1 decimal place and strip trailing zeros/dot
        return f"{k:.1f}k".replace(".0k", "k")

    if count < 1_000_000_000:
        # Millions
        m = count / 1_000_000
        if m == int(m):
            return f"{int(m)}M"
        return f"{m:.1f}M".replace(".0M", "M")

    # Billion case
    b = count / 1_000_000_000
    if b == int(b):
        return f"{int(b)}B"
    return f"{b:.1f}B".replace(".0B", "B")

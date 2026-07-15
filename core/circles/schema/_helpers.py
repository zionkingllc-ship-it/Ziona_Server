"""Leaf helpers shared across the circles schema modules.

Split from the former core/circles/schema.py (no contract change).
"""


from core.media.schema import MediaFileType
from core.shared.types import MediaType as GraphQLMediaType


def _anchor_date_value(anchor) -> str:
    """Return the ISO calendar date used for mobile anchor filtering."""
    return anchor.published_at.date().isoformat()


def _unique_anchor_dates(*anchor_groups) -> list[str]:
    """Return unique anchor dates while preserving newest-first query order."""
    seen: set[str] = set()
    dates: list[str] = []
    for group in anchor_groups:
        if not group:
            continue
        anchors = group if isinstance(group, list | tuple) else [group]
        for anchor in anchors:
            if not anchor:
                continue
            anchor_date = _anchor_date_value(anchor)
            if anchor_date not in seen:
                seen.add(anchor_date)
                dates.append(anchor_date)
    return dates


def _media_file_to_graphql(media_file) -> MediaFileType:
    from core.shared.utils import normalize_duration_seconds

    return MediaFileType(
        id=str(media_file.id),
        url=media_file.url,
        type=GraphQLMediaType[media_file.media_type.upper()],
        width=media_file.width,
        height=media_file.height,
        thumbnail_url=media_file.thumbnail_url,
        duration=normalize_duration_seconds(media_file.duration),
    )

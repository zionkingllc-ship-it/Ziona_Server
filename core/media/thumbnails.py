"""Helpers for resolving and syncing generated media thumbnails."""

import logging

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from core.media.models import MediaFile
from core.shared.utils import normalize_url

logger = logging.getLogger("core.media")

ANCHOR_MEDIA_TYPES = ("image", "image_text", "video")
ANCHOR_IMAGE_TYPES = ("image", "image_text")


def storage_path_from_media_url(url: str) -> str | None:
    """Return the object key for a media URL owned by this deployment."""
    value = normalize_url((url or "").strip()).split("?", 1)[0].split("#", 1)[0]
    if not value:
        return None

    bucket_prefix = f"https://storage.googleapis.com/{settings.GCP_STORAGE_BUCKET}/"
    if value.startswith(bucket_prefix):
        return value[len(bucket_prefix) :] or None

    if value.startswith("http://storage.googleapis.com/"):
        value = f"https://{value[len('http://') :]}"
        if value.startswith(bucket_prefix):
            return value[len(bucket_prefix) :] or None

    media_prefix = getattr(settings, "MEDIA_URL", "/media/")
    if media_prefix and value.startswith(media_prefix):
        return value[len(media_prefix) :] or None
    return None


def anchor_thumbnail_candidates(
    anchor_type: str,
    media_url: str = "",
    anchor_image: str = "",
    anchor_video: str = "",
) -> tuple[str, ...]:
    """Order the media URLs by the asset that should provide the anchor preview."""
    if anchor_type == "video":
        return (anchor_video, anchor_image, media_url)
    return (anchor_image, media_url, anchor_video)


def resolve_media_thumbnail_url(*urls: str) -> str:
    """Return the first generated thumbnail for the supplied media URLs."""
    keys: dict[str, int] = {}
    for index, url in enumerate(urls):
        value = (url or "").strip()
        if not value:
            continue
        for key in (storage_path_from_media_url(value), value):
            if key:
                keys.setdefault(key, index)

    if not keys:
        return ""

    media_files = (
        MediaFile.objects.filter(storage_path__in=list(keys))
        .exclude(thumbnail_path="")
        .only("storage_path", "thumbnail_path")
    )

    best_index: int | None = None
    best_url = ""
    for media_file in media_files:
        index = keys.get(media_file.storage_path)
        if index is None:
            continue
        thumbnail_url = media_file.thumbnail_url
        if thumbnail_url and (best_index is None or index < best_index):
            best_index = index
            best_url = thumbnail_url
    return best_url


def has_usable_anchor_thumbnail(anchor_thumbnail: str, *source_urls: str) -> bool:
    """Return False when the thumbnail is blank or is just the full-size media."""
    value = (anchor_thumbnail or "").strip()
    if not value:
        return False
    sources = {(url or "").strip() for url in source_urls if url}
    return value not in sources


def can_generate_anchor_thumbnail(
    anchor_type: str,
    media_url: str = "",
    anchor_image: str = "",
) -> bool:
    """Return True when the anchor has an image we can read from storage."""
    if anchor_type not in ANCHOR_MEDIA_TYPES:
        return False
    source_url = anchor_image
    if anchor_type in ANCHOR_IMAGE_TYPES:
        source_url = anchor_image or media_url
    return storage_path_from_media_url(source_url) is not None


def sync_anchor_thumbnails(media_file: MediaFile) -> int:
    """Backfill blank anchor thumbnails that reference a newly processed media file."""
    thumbnail_url = media_file.thumbnail_url
    if not thumbnail_url:
        return 0

    from core.circles.models import Anchor

    urls = {media_file.storage_path, media_file.url}
    if media_file.storage_path and not media_file.storage_path.startswith("http"):
        urls.add(
            f"https://storage.googleapis.com/"
            f"{settings.GCP_STORAGE_BUCKET}/{media_file.storage_path}"
        )
    urls.discard("")

    updated = (
        Anchor.objects.filter(anchor_type__in=ANCHOR_MEDIA_TYPES)
        .filter(Q(media_url__in=urls) | Q(anchor_image__in=urls) | Q(anchor_video__in=urls))
        .filter(Q(anchor_thumbnail="") | Q(anchor_thumbnail__in=urls))
        .update(anchor_thumbnail=thumbnail_url, updated_at=timezone.now())
    )

    if updated:
        logger.info(
            "anchor_thumbnails_synced",
            extra={"media_id": str(media_file.id), "count": updated},
        )
    return updated


def enqueue_anchor_thumbnail(anchor_id: str) -> None:
    """Queue thumbnail generation without allowing a broker failure to break anchor writes."""
    from core.media.tasks import generate_anchor_thumbnail

    try:
        generate_anchor_thumbnail.apply_async(args=[anchor_id])
    except Exception:  # noqa: BLE001
        logger.warning(
            "anchor_thumbnail_enqueue_failed",
            extra={"anchor_id": anchor_id},
            exc_info=True,
        )

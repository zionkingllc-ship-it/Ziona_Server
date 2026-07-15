"""Celery tasks for staged media processing."""

import logging
import os
import subprocess
import tempfile
from pathlib import Path

from billiard.exceptions import SoftTimeLimitExceeded
from celery import chain, shared_task
from django.apps import apps
from django.conf import settings
from django.db import DatabaseError
from django.utils import timezone
from google.api_core import exceptions as gcs_exceptions
from PIL import UnidentifiedImageError

from core.media.models import MediaFile, MediaStatus, MediaType
from core.shared.utils import normalize_url

logger = logging.getLogger("core.media")

from core.media.media_processing import (  # noqa: E402,F401
    _classify_processing_failure,
    _extract_video_metadata,
    _generate_video_thumbnail_file,
    _has_alpha,
    _optimize_image_file,
    _optimize_video_file,
    get_ffmpeg_runtime_info,
)

DETERMINISTIC_MEDIA_EXCEPTIONS = (
    RuntimeError,
    SoftTimeLimitExceeded,
    subprocess.TimeoutExpired,
    UnidentifiedImageError,
    ValueError,
)
TRANSIENT_MEDIA_EXCEPTIONS = (
    DatabaseError,
    gcs_exceptions.GoogleAPICallError,
    gcs_exceptions.RetryError,
)


def _media_setting(name: str, default: int) -> int:
    return int(getattr(settings, name, default))


@shared_task(
    bind=True,
    max_retries=2,
    default_retry_delay=15,
    soft_time_limit=_media_setting("MEDIA_PROCESS_TASK_SOFT_TIME_LIMIT_SECONDS", 45),
    time_limit=60,
)
def process_media_upload(self, media_id: str) -> str | None:
    """Queue a staged processing pipeline for the uploaded media."""
    media_file = _get_media_file(media_id)
    if media_file is None:
        logger.error("media_file_not_found", extra={"media_id": media_id})
        return None
    if media_file.status == MediaStatus.FAILED:
        logger.info("media_processing_skipped_failed", extra={"media_id": media_id})
        return str(media_file.id)
    if media_file.status == MediaStatus.READY:
        logger.info("media_processing_skipped_ready", extra={"media_id": media_id})
        return str(media_file.id)
    if media_file.status == MediaStatus.PENDING:
        media_file.status = MediaStatus.PROCESSING
        media_file.save(update_fields=["status", "updated_at"])

    logger.info(
        "media_processing_pipeline_start",
        extra={
            "media_id": str(media_file.id),
            "media_type": media_file.media_type,
            "user_id": str(media_file.user_id),
        },
    )

    if media_file.media_type == MediaType.IMAGE:
        pipeline = chain(
            optimize_image_media_stage.s(str(media_file.id)),
            finalize_media_ready.s(),
        )
    elif media_file.media_type == MediaType.VIDEO:
        pipeline = chain(
            optimize_video_media_stage.s(str(media_file.id)),
            generate_video_thumbnail_stage.s(),
            finalize_media_ready.s(),
        )
    else:
        _mark_media_failed(media_file, stage="pipeline", exc=ValueError("Unsupported media type"))
        raise ValueError(f"Unsupported media type: {media_file.media_type}")

    try:
        async_result = pipeline.apply_async()
    except Exception as exc:  # noqa: BLE001
        _handle_stage_failure(self, media_file, exc, stage="pipeline_enqueue")
        return None

    logger.info(
        "media_processing_pipeline_queued",
        extra={"media_id": str(media_file.id), "pipeline_task_id": async_result.id},
    )
    return str(media_file.id)


@shared_task(
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    retry_backoff=True,
    retry_jitter=True,
    soft_time_limit=_media_setting("MEDIA_IMAGE_TASK_SOFT_TIME_LIMIT_SECONDS", 150),
    time_limit=180,
)
def optimize_image_media_stage(self, media_id: str) -> str:
    """Optimize an uploaded image and persist its canonical metadata."""
    media_file = _get_media_file_or_raise(media_id)
    if media_file.status == MediaStatus.FAILED:
        return str(media_file.id)

    try:
        _optimize_image_media(media_file)
    except Exception as exc:  # noqa: BLE001
        _handle_stage_failure(self, media_file, exc, stage="image_optimize")

    return str(media_file.id)


@shared_task(
    bind=True,
    max_retries=2,
    default_retry_delay=45,
    retry_backoff=True,
    retry_jitter=True,
    soft_time_limit=_media_setting("MEDIA_VIDEO_TASK_SOFT_TIME_LIMIT_SECONDS", 270),
    time_limit=300,
)
def optimize_video_media_stage(self, media_id: str) -> str:
    """Normalize an uploaded video and persist canonical metadata."""
    media_file = _get_media_file_or_raise(media_id)
    if media_file.status == MediaStatus.FAILED:
        return str(media_file.id)

    try:
        _optimize_video_media(media_file)
    except Exception as exc:  # noqa: BLE001
        _handle_stage_failure(self, media_file, exc, stage="video_optimize")

    return str(media_file.id)


@shared_task(
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    retry_backoff=True,
    retry_jitter=True,
    soft_time_limit=_media_setting("MEDIA_THUMBNAIL_TASK_SOFT_TIME_LIMIT_SECONDS", 100),
    time_limit=120,
)
def generate_video_thumbnail_stage(self, media_id: str) -> str:
    """Generate a thumbnail from the already-optimized canonical video."""
    media_file = _get_media_file_or_raise(media_id)
    if media_file.status == MediaStatus.FAILED:
        return str(media_file.id)

    try:
        _generate_video_thumbnail(media_file)
    except Exception as exc:  # noqa: BLE001
        _handle_stage_failure(self, media_file, exc, stage="video_thumbnail")

    return str(media_file.id)


@shared_task(
    bind=True,
    max_retries=1,
    default_retry_delay=30,
    soft_time_limit=_media_setting("MEDIA_FINALIZE_TASK_SOFT_TIME_LIMIT_SECONDS", 45),
    time_limit=60,
)
def finalize_media_ready(self, media_id: str) -> str | None:
    """Mark processed media READY once every prior pipeline stage has succeeded."""
    media_file = _get_media_file(media_id)
    if media_file is None:
        logger.error("media_finalize_missing", extra={"media_id": media_id})
        return None
    if media_file.status == MediaStatus.FAILED:
        logger.info("media_finalize_skipped_failed", extra={"media_id": media_id})
        return str(media_file.id)

    try:
        media_file.status = MediaStatus.READY
        media_file.save(update_fields=["status", "updated_at"])
    except Exception as exc:  # noqa: BLE001
        _handle_stage_failure(self, media_file, exc, stage="finalize")

    logger.info("media_processing_complete", extra={"media_id": media_id, "status": "ready"})
    return str(media_file.id)


# Every model field that stores a GCS media URL as a bare string (NOT an FK to
# MediaFile). The stale-media cleanup treats an object referenced by any of these
# as in-use and must never delete it. Verified against the models — keep in sync:
# add a new (app_label, model, [fields]) row whenever a model gains a media-URL
# string field, or the cleanup could delete that feature's live images.
_REFERENCING_FIELDS: list[tuple[str, str, list[str]]] = [
    ("circles", "Circle", ["cover_image", "profile_image_url", "banner_image"]),
    (
        "circles",
        "Anchor",
        [
            "media_url",
            "anchor_image",
            "anchor_video",
            "anchor_thumbnail",
            "background_image",
            "preview_url",
        ],
    ),
    ("circles", "AnchorPage", ["media_url"]),
    ("circles", "AnchorResponse", ["media_url"]),
    ("circles", "CirclePost", ["image_url", "media_url"]),
    ("posts", "PostMedia", ["media_url", "thumbnail_url"]),
    ("users", "User", ["avatar_url"]),
    ("categories", "Category", ["icon"]),
    ("engagement", "BookmarkFolder", ["thumbnail_url"]),
]


def _public_url(storage_path: str) -> str:
    """Canonical GCS public URL for an object key.

    Matches both what content stores (built in media/services.py) and what
    MediaFile.url returns; normalize_url is a no-op for a clean single-prefix URL.
    """
    return normalize_url(
        f"https://storage.googleapis.com/{settings.GCP_STORAGE_BUCKET}/{storage_path}"
    )


def _resolve_referenced_object_strings(candidates: list[MediaFile]) -> set[str]:
    """Return the storage-path/URL strings (among ``candidates``) still referenced
    by live content or by another MediaFile row (twin-blob), via a small fixed
    number of bulk queries. A candidate is in use if its object key OR its public
    URL appears in the returned set.
    """
    keys = {m.storage_path for m in candidates if m.storage_path}
    if not keys:
        return set()
    urls = {_public_url(k) for k in keys}
    candidate_ids = [m.id for m in candidates]
    referenced: set[str] = set()

    # Twin-blob: a *different* MediaFile row (e.g. a READY `media_urls` row whose
    # storage_path is the full URL) points at the same physical object.
    referenced.update(
        MediaFile.objects.filter(storage_path__in=(keys | urls))
        .exclude(id__in=candidate_ids)
        .values_list("storage_path", flat=True)
    )

    # Content models that store a bare GCS media URL string.
    for app_label, model_name, fields in _REFERENCING_FIELDS:
        try:
            model = apps.get_model(app_label, model_name)
        except LookupError:  # model/app renamed — skip rather than crash the cron
            logger.warning("stale_cleanup_unknown_model", extra={"model": model_name})
            continue
        for field in fields:
            referenced.update(
                model.objects.filter(**{f"{field}__in": urls}).values_list(field, flat=True)
            )
    return referenced


@shared_task(
    bind=True,
    max_retries=1,
    default_retry_delay=60,
    soft_time_limit=_media_setting("MEDIA_CLEANUP_TASK_SOFT_TIME_LIMIT_SECONDS", 150),
    time_limit=180,
)
def cleanup_stale_media_uploads(self) -> int:
    """Remove abandoned GCS uploads — but never a blob still referenced by content.

    Selects stale PENDING/PROCESSING media. For each, if its object is still
    referenced by live content (or another MediaFile row), it is left in GCS and
    the row is self-healed to READY (so it drops out of future batches). Only
    genuinely unreferenced objects have their blob deleted and row marked FAILED.
    """
    stale_minutes = _media_setting("MEDIA_STALE_UPLOAD_MINUTES", 15)
    cutoff = timezone.now() - timezone.timedelta(minutes=stale_minutes)
    stale_media = list(
        MediaFile.objects.filter(
            status__in=[MediaStatus.PENDING, MediaStatus.PROCESSING],
            updated_at__lt=cutoff,
        )[:250]
    )
    if not stale_media:
        return 0

    deleted = 0
    healed = 0
    try:
        referenced = _resolve_referenced_object_strings(stale_media)
        bucket = _get_gcs_bucket()
        for media_file in stale_media:
            key = media_file.storage_path
            in_use = bool(key) and (key in referenced or _public_url(key) in referenced)

            if in_use:
                # Referenced by live content — protect the object and self-heal the
                # row so it is excluded from future cleanup batches.
                if media_file.status != MediaStatus.READY:
                    media_file.status = MediaStatus.READY
                    media_file.save(update_fields=["status", "updated_at"])
                healed += 1
                continue

            if key:
                try:
                    bucket.blob(key).delete()
                    deleted += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "stale_media_blob_delete_failed",
                        extra={"media_id": str(media_file.id), "error": str(exc)},
                    )
            _mark_media_failed(
                media_file,
                stage="stale_cleanup",
                exc=subprocess.TimeoutExpired("media processing", stale_minutes * 60),
            )
    except Exception as exc:  # noqa: BLE001
        logger.error("stale_media_cleanup_failed", exc_info=True)
        raise self.retry(exc=exc) from exc

    logger.info(
        "stale_media_cleanup_complete",
        extra={"count": len(stale_media), "deleted": deleted, "healed": healed},
    )
    return len(stale_media)


def _optimize_image_media(media_file: MediaFile) -> None:
    """Download, optimize, and replace an image blob in GCS."""
    suffix = Path(media_file.storage_path).suffix or ".img"
    with tempfile.TemporaryDirectory() as tmpdir:
        original_path = Path(tmpdir) / f"original{suffix}"
        optimized_path = Path(tmpdir) / f"optimized{suffix}"

        _download_blob(media_file.storage_path, original_path)
        content_type, width, height = _optimize_image_file(
            original_path,
            optimized_path,
            media_file.file_type,
        )
        _upload_blob(media_file.storage_path, optimized_path, content_type)

        media_file.file_type = content_type
        media_file.file_size = optimized_path.stat().st_size
        media_file.width = width
        media_file.height = height
        media_file.save(update_fields=["file_type", "file_size", "width", "height", "updated_at"])


def _optimize_video_media(media_file: MediaFile) -> None:
    """Download, normalize, and replace a video blob in GCS."""
    suffix = Path(media_file.storage_path).suffix or ".mp4"
    with tempfile.TemporaryDirectory() as tmpdir:
        original_path = Path(tmpdir) / f"original{suffix}"
        optimized_path = Path(tmpdir) / "optimized.mp4"

        _download_blob(media_file.storage_path, original_path)
        _optimize_video_file(original_path, optimized_path)
        _upload_blob(media_file.storage_path, optimized_path, "video/mp4")
        width, height, duration = _extract_video_metadata(optimized_path)

        media_file.file_type = "video/mp4"
        media_file.file_size = optimized_path.stat().st_size
        media_file.width = width
        media_file.height = height
        media_file.duration = duration
        media_file.save(
            update_fields=["file_type", "file_size", "width", "height", "duration", "updated_at"]
        )


def _get_gcs_bucket():
    from google.cloud import storage

    if settings.GCP_CREDENTIALS_FILE and os.path.exists(settings.GCP_CREDENTIALS_FILE):
        client = storage.Client.from_service_account_json(settings.GCP_CREDENTIALS_FILE)
    else:
        client = storage.Client()
    return client.bucket(settings.GCP_STORAGE_BUCKET)


def _download_blob(storage_path: str, destination: Path) -> None:
    bucket = _get_gcs_bucket()
    bucket.blob(storage_path).download_to_filename(str(destination))


def _upload_blob(storage_path: str, source: Path, content_type: str) -> None:
    bucket = _get_gcs_bucket()
    bucket.blob(storage_path).upload_from_filename(str(source), content_type=content_type)


def _generate_video_thumbnail(media_file: MediaFile) -> None:
    """Extract a square JPEG thumbnail from a locally downloaded optimized video."""
    with tempfile.TemporaryDirectory() as tmpdir:
        optimized_video_path = Path(tmpdir) / "optimized.mp4"
        thumbnail_file_path = Path(tmpdir) / "thumbnail.jpg"

        _download_blob(media_file.storage_path, optimized_video_path)
        _generate_video_thumbnail_file(optimized_video_path, thumbnail_file_path)

        thumbnail_path = f"thumbnails/{media_file.user_id}/{media_file.id}.jpg"
        _upload_blob(thumbnail_path, thumbnail_file_path, "image/jpeg")

        media_file.thumbnail_path = thumbnail_path
        media_file.save(update_fields=["thumbnail_path", "updated_at"])

        logger.info(
            "thumbnail_generated",
            extra={
                "media_id": str(media_file.id),
                "thumbnail_path": thumbnail_path,
                "size_bytes": thumbnail_file_path.stat().st_size,
            },
        )


def _get_media_file(media_id: str) -> MediaFile | None:
    try:
        return MediaFile.objects.get(id=media_id)
    except MediaFile.DoesNotExist:
        return None


def _get_media_file_or_raise(media_id: str) -> MediaFile:
    media_file = _get_media_file(media_id)
    if media_file is None:
        raise ValueError(f"Media file {media_id} does not exist")
    return media_file


def _handle_stage_failure(task, media_file: MediaFile, exc: Exception, *, stage: str) -> None:
    if isinstance(exc, DETERMINISTIC_MEDIA_EXCEPTIONS):
        _mark_media_failed(media_file, stage=stage, exc=exc)
        raise exc

    if isinstance(exc, TRANSIENT_MEDIA_EXCEPTIONS):
        try:
            raise task.retry(exc=exc)
        except task.MaxRetriesExceededError:
            _mark_media_failed(media_file, stage=stage, exc=exc)
            raise exc from exc

    _mark_media_failed(media_file, stage=stage, exc=exc)
    raise exc from exc


def _mark_media_failed(media_file: MediaFile, *, stage: str, exc: Exception) -> None:
    code, message = _classify_processing_failure(media_file, exc)
    media_file.status = MediaStatus.FAILED
    media_file.processing_error_code = code
    media_file.processing_error_message = message
    media_file.processing_failed_stage = stage
    media_file.save(
        update_fields=[
            "status",
            "processing_error_code",
            "processing_error_message",
            "processing_failed_stage",
            "updated_at",
        ]
    )
    logger.error(
        "media_processing_failed",
        extra={
            "media_id": str(media_file.id),
            "stage": stage,
            "code": code,
            "error": str(exc),
        },
        exc_info=True,
    )

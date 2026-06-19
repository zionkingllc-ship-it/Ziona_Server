"""Celery tasks for staged media processing."""

import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path

from celery import chain, shared_task
from django.conf import settings
from django.db import DatabaseError
from django.utils import timezone
from google.api_core import exceptions as gcs_exceptions
from PIL import UnidentifiedImageError

from core.media.models import MediaFile, MediaStatus, MediaType

logger = logging.getLogger("core.media")


DETERMINISTIC_MEDIA_EXCEPTIONS = (
    RuntimeError,
    subprocess.TimeoutExpired,
    UnidentifiedImageError,
    ValueError,
)
TRANSIENT_MEDIA_EXCEPTIONS = (
    DatabaseError,
    gcs_exceptions.GoogleAPICallError,
    gcs_exceptions.RetryError,
)


@shared_task(bind=True, max_retries=2, default_retry_delay=15, time_limit=60)
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


@shared_task(bind=True, max_retries=1, default_retry_delay=30, time_limit=60)
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


@shared_task(bind=True, max_retries=1, default_retry_delay=60, time_limit=180)
def cleanup_stale_media_uploads(self) -> int:
    """Mark stale unprocessed media failed and remove abandoned GCS objects."""
    cutoff = timezone.now() - timezone.timedelta(hours=settings.MEDIA_STALE_UPLOAD_HOURS)
    stale_media = list(
        MediaFile.objects.filter(
            status__in=[MediaStatus.PENDING, MediaStatus.PROCESSING],
            updated_at__lt=cutoff,
        )[:250]
    )
    deleted = 0
    try:
        bucket = _get_gcs_bucket()
        for media_file in stale_media:
            if media_file.storage_path:
                try:
                    bucket.blob(media_file.storage_path).delete()
                    deleted += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "stale_media_blob_delete_failed",
                        extra={"media_id": str(media_file.id), "error": str(exc)},
                    )
            media_file.status = MediaStatus.FAILED
            media_file.save(update_fields=["status", "updated_at"])
    except Exception as exc:  # noqa: BLE001
        logger.error("stale_media_cleanup_failed", exc_info=True)
        raise self.retry(exc=exc) from exc

    logger.info(
        "stale_media_cleanup_complete", extra={"count": len(stale_media), "deleted": deleted}
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


def _optimize_image_file(
    input_path: Path,
    output_path: Path,
    content_type: str,
) -> tuple[str, int, int]:
    """Resize and compress an image file, returning content type and dimensions."""
    from PIL import Image, ImageOps

    with Image.open(input_path) as image:
        image = ImageOps.exif_transpose(image)
        image.thumbnail(
            (settings.MEDIA_IMAGE_MAX_DIMENSION, settings.MEDIA_IMAGE_MAX_DIMENSION),
            Image.Resampling.LANCZOS,
        )
        width, height = image.size

        if content_type in {"image/jpeg", "image/jpg"}:
            optimized = image.convert("RGB")
            optimized.save(
                output_path,
                format="JPEG",
                quality=settings.MEDIA_IMAGE_JPEG_QUALITY,
                optimize=True,
                progressive=True,
            )
            return "image/jpeg", width, height

        if content_type == "image/webp":
            optimized = image.convert("RGBA" if _has_alpha(image) else "RGB")
            optimized.save(
                output_path,
                format="WEBP",
                quality=settings.MEDIA_IMAGE_JPEG_QUALITY,
                method=6,
            )
            return "image/webp", width, height

        optimized = image.convert("RGBA" if _has_alpha(image) else "RGB")
        optimized.save(output_path, format="PNG", optimize=True, compress_level=9)
        return "image/png", width, height


def _optimize_video_file(input_path: Path, output_path: Path) -> None:
    """Normalize video into a cost-conscious MP4 delivery profile."""
    import imageio_ffmpeg

    ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
    scale_filter = (
        "scale="
        f"if(gt(iw\\,ih)\\,min({settings.MEDIA_VIDEO_MAX_DIMENSION}\\,iw)\\,-2):"
        f"if(gt(iw\\,ih)\\,-2\\,min({settings.MEDIA_VIDEO_MAX_DIMENSION}\\,ih))"
    )
    cmd = [
        ffmpeg_bin,
        "-y",
        "-i",
        str(input_path),
        "-map_metadata",
        "-1",
        "-vf",
        scale_filter,
        "-c:v",
        "libx264",
        "-preset",
        settings.MEDIA_VIDEO_PRESET,
        "-crf",
        str(settings.MEDIA_VIDEO_CRF),
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-threads",
        "1",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        str(output_path),
    ]
    result = subprocess.run(
        cmd,  # noqa: S603
        capture_output=True,
        timeout=settings.MEDIA_VIDEO_OPTIMIZE_TIMEOUT_SECONDS,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"FFmpeg video optimization failed: {stderr[:500]}")


def _extract_video_metadata(video_path: Path) -> tuple[int | None, int | None, float | None]:
    """Extract normalized video width, height, and duration using bundled FFmpeg."""
    import imageio_ffmpeg

    ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
    result = subprocess.run(
        [ffmpeg_bin, "-i", str(video_path)],  # noqa: S603
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    stderr = result.stderr or ""

    width = height = None
    duration = None

    duration_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", stderr)
    if duration_match:
        hours = int(duration_match.group(1))
        minutes = int(duration_match.group(2))
        seconds = float(duration_match.group(3))
        duration = round((hours * 3600) + (minutes * 60) + seconds, 3)

    for line in stderr.splitlines():
        if "Video:" not in line:
            continue
        dimensions_match = re.search(r"(\d{2,5})x(\d{2,5})", line)
        if dimensions_match:
            width = int(dimensions_match.group(1))
            height = int(dimensions_match.group(2))
            break

    return width, height, duration


def _has_alpha(image) -> bool:
    if image.mode in {"RGBA", "LA"}:
        return True
    return image.mode == "P" and "transparency" in image.info


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


def _generate_video_thumbnail_file(input_path: Path, output_path: Path) -> None:
    """Generate a thumbnail image from a local optimized video file."""
    import imageio_ffmpeg

    ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        ffmpeg_bin,
        "-y",
        "-ss",
        "1",
        "-i",
        str(input_path),
        "-vframes",
        "1",
        "-vf",
        "crop=min(iw\\,ih):min(iw\\,ih),scale=640:640",
        "-f",
        "image2",
        "-vcodec",
        "mjpeg",
        "-loglevel",
        "error",
        "-threads",
        "1",
        str(output_path),
    ]
    result = subprocess.run(
        cmd,  # noqa: S603
        capture_output=True,
        timeout=settings.MEDIA_THUMBNAIL_TIMEOUT_SECONDS,
        check=False,
    )

    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"FFmpeg exited with code {result.returncode}: {stderr[:500]}")
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError("FFmpeg produced an empty thumbnail image")


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


def get_ffmpeg_runtime_info() -> dict[str, str | None]:
    """Return the resolved FFmpeg binary path and version banner."""
    import imageio_ffmpeg

    ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [ffmpeg_bin, "-version"]
    version_result = subprocess.run(
        cmd,  # noqa: S603
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    version_line = None
    if version_result.stdout:
        version_line = version_result.stdout.splitlines()[0].strip()
    return {"path": ffmpeg_bin, "version": version_line}


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


def _classify_processing_failure(media_file: MediaFile, exc: Exception) -> tuple[str, str]:
    """Return a stable client-safe code/message for a media processing failure."""
    media_kind = "Video" if media_file.media_type == MediaType.VIDEO else "Media"
    error_text = str(exc)
    lowered_error = error_text.lower()

    if isinstance(exc, subprocess.TimeoutExpired):
        return (
            "VIDEO_PROCESSING_TIMEOUT"
            if media_file.media_type == MediaType.VIDEO
            else "MEDIA_PROCESSING_TIMEOUT",
            f"{media_kind} processing timed out. Please try a shorter or lower-resolution file.",
        )

    if media_file.media_type == MediaType.VIDEO and (
        "code -11" in lowered_error
        or "sigsegv" in lowered_error
        or "signal 11" in lowered_error
        or "out of memory" in lowered_error
        or "oom" in lowered_error
    ):
        return (
            "VIDEO_PROCESSING_RESOURCE_LIMIT",
            "Video processing exceeded available server resources. Please try a shorter or lower-resolution video.",
        )

    if isinstance(exc, UnidentifiedImageError):
        return (
            "IMAGE_PROCESSING_FAILED",
            "Image processing failed. Please upload a valid image file.",
        )

    if media_file.media_type == MediaType.VIDEO and "ffmpeg" in lowered_error:
        return (
            "VIDEO_PROCESSING_FAILED",
            "Video processing failed. Please try a different video format.",
        )

    return (
        "MEDIA_PROCESSING_FAILED",
        f"{media_kind} processing failed. Please try another file.",
    )

"""
Celery tasks for media processing.

Handles post-upload file validation and processing.
"""

import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from core.media.models import MediaFile, MediaStatus, MediaType

logger = logging.getLogger("core.media")


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    time_limit=300,
)
def process_media_upload(self, media_id: str) -> None:
    """Process an uploaded media file.

    Steps:
    1. Validate the MediaFile record exists.
    2. Optimize and replace the canonical GCS object.
    3. For videos: generate a thumbnail.
    4. Update status to READY only after processing succeeds.

    Args:
        media_id: UUID of the MediaFile to process.
    """
    try:
        media_file = MediaFile.objects.get(id=media_id)
    except MediaFile.DoesNotExist:
        logger.error("Media file not found: %s", media_id)
        return

    logger.info(
        "Processing media upload",
        extra={
            "media_id": media_id,
            "media_type": media_file.media_type,
            "user_id": str(media_file.user_id),
        },
    )

    try:
        if media_file.status == MediaStatus.PENDING:
            media_file.status = MediaStatus.PROCESSING
            media_file.save(update_fields=["status", "updated_at"])

        if media_file.media_type == MediaType.IMAGE:
            _optimize_image_media(media_file)

        if media_file.media_type == MediaType.VIDEO:
            _optimize_video_media(media_file)
            _generate_video_thumbnail(media_file)

        media_file.status = MediaStatus.READY
        media_file.save(update_fields=["status", "updated_at"])

        logger.info(
            "Media processing complete",
            extra={"media_id": media_id, "status": "ready"},
        )

    except Exception as exc:
        logger.error(
            "Media processing failed for %s: %s",
            media_id,
            exc,
            exc_info=True,
        )
        media_file.status = MediaStatus.FAILED
        media_file.save(update_fields=["status", "updated_at"])

        # Retry on catastrophic failure (DB unavailable, OOM, etc.)
        raise self.retry(exc=exc) from exc


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
    except Exception as exc:
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
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        str(output_path),
    ]
    result = subprocess.run(
        cmd,  # noqa: S603
        capture_output=True,
        timeout=240,
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
    """Extract a square JPEG thumbnail from a video and upload it to GCS.

    Design principles:
    - Streams from GCS via a signed URL — never downloads the full video.
    - Captures JPEG bytes from FFmpeg stdout via a pipe — no temp files on disk.
    - Uploads directly server-to-GCS using the storage client — no signed URL needed.

    FFmpeg command breakdown:
        -ss 1           Seek to the 1-second mark (avoids black frames on many videos)
        -i <url>        Read from the GCS signed URL (FFmpeg handles HTTP streaming)
        -vframes 1      Extract exactly one frame
        -vf "crop=..."  Center-crop to a square, then scale to 640×640
        -vcodec mjpeg   Encode as JPEG
        -f image2       Force image output format
        pipe:1          Write raw JPEG bytes to stdout (no disk writes)

    Args:
        media_file: The MediaFile instance to generate a thumbnail for.

    Raises:
        RuntimeError: If FFmpeg fails or produces no output.
        Exception: On GCS upload failure.
    """
    from core.media.services import _generate_gcp_signed_url

    # -----------------------------------------------------------------------
    # Step 1: Generate a short-lived signed READ URL for the video.
    # This allows FFmpeg to stream the first few seconds directly from GCS
    # without downloading the entire file.
    # -----------------------------------------------------------------------
    # If storage_path is already a full URL (e.g. manual entry or external import),
    # use it directly. Otherwise, generate a signed URL.
    if media_file.storage_path.startswith(("http://", "https://")):
        video_url = media_file.storage_path
    else:
        try:
            video_url = _generate_gcp_signed_url(
                bucket=settings.GCP_STORAGE_BUCKET,
                blob_path=media_file.storage_path,
                content_type=media_file.file_type,
                expiry_seconds=900,  # 15 min — sufficient for FFmpeg to connect
                method="GET",
            )
        except Exception as e:
            # Fall back to the public URL if signed URL generation fails.
            # This works as long as the bucket has public read access.
            logger.warning("Signed URL generation failed, falling back to public URL: %s", e)
            video_url = (
                f"https://storage.googleapis.com/"
                f"{settings.GCP_STORAGE_BUCKET}/{media_file.storage_path}"
            )

    # -----------------------------------------------------------------------
    # Step 2: Run FFmpeg to extract a single square JPEG frame.
    # stdout=PIPE captures the JPEG bytes directly in memory.
    # stderr=PIPE silences FFmpeg's verbose output from polluting logs.
    # timeout=60 ensures a hung FFmpeg process never blocks a worker forever.
    #
    # imageio_ffmpeg.get_ffmpeg_exe() returns the absolute path to the
    # pip-bundled static FFmpeg binary — no system install required.
    # -----------------------------------------------------------------------
    import imageio_ffmpeg

    ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        ffmpeg_bin,
        "-ss",
        "1",  # Seek to 1s
        "-i",
        video_url,  # Read from GCS
        "-vframes",
        "1",  # One frame only
        "-vf",
        "crop=min(iw\\,ih):min(iw\\,ih),scale=640:640",  # Square + resize
        "-f",
        "image2",  # Image format
        "-vcodec",
        "mjpeg",  # JPEG codec
        "-loglevel",
        "error",  # Suppress noise
        "pipe:1",  # Output to stdout
    ]
    result = subprocess.run(
        cmd,  # noqa: S603
        capture_output=True,
        timeout=60,
        check=False,
    )

    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"FFmpeg exited with code {result.returncode}: {stderr[:500]}")

    jpeg_bytes = result.stdout
    if not jpeg_bytes:
        raise RuntimeError("FFmpeg produced empty output — video may be corrupt or too short")

    logger.debug(
        "FFmpeg frame extracted",
        extra={"media_id": str(media_file.id), "jpeg_size_bytes": len(jpeg_bytes)},
    )

    # -----------------------------------------------------------------------
    # Step 3: Upload the JPEG bytes directly to GCS.
    # Server-to-GCS uploads use the storage client directly — no signed URL
    # needed. This is faster and does not count against Upstash.
    # -----------------------------------------------------------------------
    thumbnail_path = f"thumbnails/{media_file.user_id}/{media_file.id}.jpg"

    bucket = _get_gcs_bucket()
    blob = bucket.blob(thumbnail_path)
    blob.upload_from_string(jpeg_bytes, content_type="image/jpeg")

    # -----------------------------------------------------------------------
    # Step 4: Persist the thumbnail path on the MediaFile record.
    # The thumbnail_url property on the model already knows how to build
    # the correct GCS URL from this relative path.
    # -----------------------------------------------------------------------
    media_file.thumbnail_path = thumbnail_path
    media_file.save(update_fields=["thumbnail_path", "updated_at"])

    logger.info(
        "Thumbnail generated and uploaded",
        extra={
            "media_id": str(media_file.id),
            "thumbnail_path": thumbnail_path,
            "size_bytes": len(jpeg_bytes),
        },
    )

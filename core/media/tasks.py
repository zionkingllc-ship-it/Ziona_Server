"""
Celery tasks for media processing.

Handles post-upload file validation and processing.
"""

import logging
import subprocess

from celery import shared_task
from django.conf import settings

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
    2. If video: attempt thumbnail generation (failure is non-fatal).
    3. Update status to READY.
    4. On catastrophic failure: mark FAILED and retry.

    Thumbnail generation is intentionally wrapped in its own try/except.
    If FFmpeg fails or GCS upload fails, the video is still marked READY
    and served normally — the mobile client handles thumbnailUrl=None.

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
        # Thumbnail generation is non-fatal — a failure here must never
        # prevent the video from being marked READY and viewable.
        if media_file.media_type == MediaType.VIDEO:
            try:
                _generate_video_thumbnail(media_file)
            except Exception as thumb_exc:
                logger.error(
                    "Thumbnail generation failed — video will be READY without thumbnail",
                    extra={"media_id": media_id, "error": str(thumb_exc)},
                    exc_info=True,
                )

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
    from google.cloud import storage

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

    if settings.GCP_CREDENTIALS_FILE:
        gcs_client = storage.Client.from_service_account_json(settings.GCP_CREDENTIALS_FILE)
    else:
        gcs_client = storage.Client()  # Uses Application Default Credentials

    bucket = gcs_client.bucket(settings.GCP_STORAGE_BUCKET)
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

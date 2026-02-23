"""
Celery tasks for media processing.

Handles post-upload file validation and processing.
"""

import logging

from celery import shared_task

from core.media.models import MediaFile, MediaStatus

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
    1. Validate the file exists in GCP
    2. Validate magic bytes
    3. If video: generate thumbnail
    4. Update status to READY or FAILED

    Args:
        media_id: UUID of the MediaFile to process.
    """
    try:
        media_file = MediaFile.objects.get(id=media_id)
    except MediaFile.DoesNotExist:
        logger.error(f"Media file not found: {media_id}")
        return

    logger.info(
        "Processing media upload",
        extra={
            "media_id": media_id,
            "file_type": media_file.file_type,
            "user_id": str(media_file.user_id),
        },
    )

    try:
        # For now, mark as ready (full processing in future milestones)
        # TODO: Validate file exists in GCP bucket
        # TODO: Validate magic bytes on actual file content
        # TODO: Generate video thumbnails
        # TODO: Video compression

        media_file.status = MediaStatus.READY
        media_file.save(update_fields=["status", "updated_at"])

        logger.info(
            "Media processing complete",
            extra={"media_id": media_id, "status": "ready"},
        )

    except Exception as exc:
        logger.error(
            f"Media processing failed for {media_id}: {exc}",
            exc_info=True,
        )
        media_file.status = MediaStatus.FAILED
        media_file.save(update_fields=["status", "updated_at"])

        # Retry on failure
        self.retry(exc=exc)

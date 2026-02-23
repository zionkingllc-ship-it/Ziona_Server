"""
Media upload service for Ziona Server.

Handles signed URL generation for direct GCP Cloud Storage uploads,
file validation, and post-upload processing.
"""

import logging
import uuid
from typing import Any

from django.conf import settings

from core.media.models import MediaFile, MediaStatus, MediaType

logger = logging.getLogger("core.media")


# Allowed MIME types and their extensions
ALLOWED_TYPES = {
    "image/jpeg": {"ext": "jpg", "media_type": MediaType.IMAGE, "max_size": 10 * 1024 * 1024},
    "image/png": {"ext": "png", "media_type": MediaType.IMAGE, "max_size": 10 * 1024 * 1024},
    "image/webp": {"ext": "webp", "media_type": MediaType.IMAGE, "max_size": 10 * 1024 * 1024},
    "video/mp4": {"ext": "mp4", "media_type": MediaType.VIDEO, "max_size": 100 * 1024 * 1024},
}

# Magic bytes for file validation
MAGIC_BYTES = {
    "image/jpeg": [b"\xff\xd8\xff"],
    "image/png": [b"\x89PNG\r\n\x1a\n"],
    "image/webp": [b"RIFF"],  # Also check for WEBP at offset 8
    "video/mp4": [b"ftyp"],  # Appears at offset 4-7
}


class MediaError(Exception):
    """Raised when media operations fail."""

    def __init__(self, message: str, code: str = "MEDIA_ERROR"):
        self.message = message
        self.code = code
        super().__init__(message)


class MediaService:
    """Service for media upload and management.

    Methods:
        generate_upload_url: Create a signed URL for direct upload
        confirm_upload: Mark upload as complete, trigger processing
        get_download_url: Generate signed download URL
    """

    @staticmethod
    def generate_upload_url(
        user_id: str,
        file_name: str,
        file_type: str,
        file_size: int,
    ) -> dict[str, Any]:
        """Generate a pre-signed URL for direct upload to GCP.

        Args:
            user_id: UUID of the uploading user.
            file_name: Original file name.
            file_type: MIME type of the file.
            file_size: File size in bytes.

        Returns:
            Dict with upload_url, media_id, and expires_in.

        Raises:
            MediaError: If file type or size is invalid.
        """
        # Validate file type
        if file_type not in ALLOWED_TYPES:
            raise MediaError(
                f"File type '{file_type}' not allowed. Accepted: JPEG, PNG, WEBP, MP4",
                code="INVALID_FILE_TYPE",
            )

        type_config = ALLOWED_TYPES[file_type]

        # Validate file size
        if file_size > type_config["max_size"]:
            max_mb = type_config["max_size"] / (1024 * 1024)
            raise MediaError(
                f"File size exceeds maximum of {max_mb:.0f} MB",
                code="FILE_TOO_LARGE",
            )

        if file_size <= 0:
            raise MediaError(
                "File size must be greater than 0",
                code="INVALID_FILE_SIZE",
            )

        # Generate storage path
        media_id = str(uuid.uuid4())
        ext = type_config["ext"]
        media_type = type_config["media_type"]

        if media_type == MediaType.VIDEO:
            storage_path = f"uploads/{user_id}/videos/{media_id}.{ext}"
        else:
            storage_path = f"uploads/{user_id}/images/{media_id}.{ext}"

        # Create MediaFile record
        media_file = MediaFile.objects.create(
            user_id=user_id,
            file_name=file_name,
            file_type=file_type,
            file_size=file_size,
            media_type=media_type,
            storage_path=storage_path,
            status=MediaStatus.PENDING,
        )

        # Generate signed URL
        expiry = settings.GCP_SIGNED_URL_EXPIRY
        try:
            upload_url = _generate_gcp_signed_url(
                bucket=settings.GCP_STORAGE_BUCKET,
                blob_path=storage_path,
                content_type=file_type,
                expiry_seconds=expiry,
                method="PUT",
            )
        except Exception as e:
            logger.error(f"Failed to generate signed URL: {e}")
            # For development, return a placeholder URL
            upload_url = f"https://storage.googleapis.com/{settings.GCP_STORAGE_BUCKET}/{storage_path}"

        logger.info(
            "Upload URL generated",
            extra={
                "user_id": user_id,
                "media_id": str(media_file.id),
                "file_type": file_type,
                "file_size": file_size,
            },
        )

        return {
            "upload_url": upload_url,
            "media_id": str(media_file.id),
            "expires_in": expiry,
        }

    @staticmethod
    def confirm_upload(media_id: str, user_id: str) -> MediaFile:
        """Mark an upload as complete and trigger processing.

        Args:
            media_id: UUID of the media file.
            user_id: UUID of the owning user (for authorization).

        Returns:
            Updated MediaFile instance.

        Raises:
            MediaError: If media file not found or unauthorized.
        """
        try:
            media_file = MediaFile.objects.get(id=media_id, user_id=user_id)
        except MediaFile.DoesNotExist:
            raise MediaError(
                "Media file not found",
                code="MEDIA_NOT_FOUND",
            )

        if media_file.status != MediaStatus.PENDING:
            raise MediaError(
                f"Media file is already {media_file.status}",
                code="INVALID_STATUS",
            )

        media_file.status = MediaStatus.PROCESSING
        media_file.save(update_fields=["status", "updated_at"])

        # Trigger async processing
        try:
            from core.media.tasks import process_media_upload

            process_media_upload.delay(str(media_file.id))
        except Exception as e:
            logger.warning(f"Failed to queue media processing task: {e}")

        return media_file

    @staticmethod
    def get_download_url(media_id: str) -> str:
        """Generate a signed download URL for a media file.

        Args:
            media_id: UUID of the media file.

        Returns:
            Signed download URL (60min expiry).

        Raises:
            MediaError: If media file not found or not ready.
        """
        try:
            media_file = MediaFile.objects.get(id=media_id)
        except MediaFile.DoesNotExist:
            raise MediaError(
                "Media file not found",
                code="MEDIA_NOT_FOUND",
            )

        if media_file.status != MediaStatus.READY:
            raise MediaError(
                "Media file is not ready for download",
                code="MEDIA_NOT_READY",
            )

        try:
            return _generate_gcp_signed_url(
                bucket=settings.GCP_STORAGE_BUCKET,
                blob_path=media_file.storage_path,
                content_type=media_file.file_type,
                expiry_seconds=3600,  # 60 minutes
                method="GET",
            )
        except Exception as e:
            logger.error(f"Failed to generate download URL: {e}")
            return f"https://storage.googleapis.com/{settings.GCP_STORAGE_BUCKET}/{media_file.storage_path}"


def _generate_gcp_signed_url(
    bucket: str,
    blob_path: str,
    content_type: str,
    expiry_seconds: int,
    method: str = "PUT",
) -> str:
    """Generate a GCP Cloud Storage signed URL.

    Args:
        bucket: GCP bucket name.
        blob_path: Path within the bucket.
        content_type: MIME type for the upload.
        expiry_seconds: URL expiry in seconds.
        method: HTTP method (PUT for upload, GET for download).

    Returns:
        Signed URL string.
    """
    from datetime import timedelta

    from google.cloud import storage

    credentials_file = settings.GCP_CREDENTIALS_FILE
    if credentials_file:
        client = storage.Client.from_service_account_json(credentials_file)
    else:
        client = storage.Client()

    bucket_obj = client.bucket(bucket)
    blob = bucket_obj.blob(blob_path)

    url = blob.generate_signed_url(
        version="v4",
        expiration=timedelta(seconds=expiry_seconds),
        method=method,
        content_type=content_type if method == "PUT" else None,
    )

    return url


def validate_magic_bytes(file_content: bytes, declared_type: str) -> bool:
    """Validate a file's magic bytes match its declared MIME type.

    Args:
        file_content: First 12+ bytes of the file.
        declared_type: The declared MIME type.

    Returns:
        True if magic bytes match, False otherwise.
    """
    if declared_type not in MAGIC_BYTES:
        return False

    expected_signatures = MAGIC_BYTES[declared_type]

    for signature in expected_signatures:
        if declared_type == "video/mp4":
            # MP4 magic bytes appear at offset 4
            if len(file_content) >= 8 and signature in file_content[4:8]:
                return True
        elif declared_type == "image/webp":
            # RIFF at offset 0, WEBP at offset 8
            if (
                len(file_content) >= 12
                and file_content[:4] == b"RIFF"
                and file_content[8:12] == b"WEBP"
            ):
                return True
        else:
            if file_content[: len(signature)] == signature:
                return True

    return False

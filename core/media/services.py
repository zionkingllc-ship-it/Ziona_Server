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
from core.shared.utils import normalize_url

logger = logging.getLogger("core.media")


ALLOWED_TYPES = {
    "image/jpeg": {"ext": "jpg", "media_type": MediaType.IMAGE, "max_size": 10 * 1024 * 1024},
    "image/png": {"ext": "png", "media_type": MediaType.IMAGE, "max_size": 10 * 1024 * 1024},
    "image/jpg": {"ext": "jpg", "media_type": MediaType.IMAGE, "max_size": 10 * 1024 * 1024},
    "image/webp": {"ext": "webp", "media_type": MediaType.IMAGE, "max_size": 10 * 1024 * 1024},
    "video/mp4": {"ext": "mp4", "media_type": MediaType.VIDEO, "max_size": 50 * 1024 * 1024},
    "video/quicktime": {"ext": "mov", "media_type": MediaType.VIDEO, "max_size": 50 * 1024 * 1024},
}

MAGIC_BYTES = {
    "image/jpeg": [b"\xff\xd8\xff"],
    "image/png": [b"\x89PNG\r\n\x1a\n"],
    "image/webp": [b"RIFF"],
    "video/mp4": [b"ftyp"],
}


class MediaError(Exception):
    """Raised when media operations fail."""

    def __init__(
        self,
        message: str,
        code: str = "MEDIA_ERROR",
        field: str | None = None,
        details: dict | None = None,
    ):
        self.message = message
        self.code = code
        self.field = field
        self.details = details
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
            Dict with upload_url, media_id, media_url, and expires_in.

        Raises:
            MediaError: If validation fails or the signed URL cannot be generated.
        """
        if file_type not in ALLOWED_TYPES:
            raise MediaError(
                f"File type '{file_type}' not allowed. Accepted: JPEG, PNG, WEBP, MP4",
                code="INVALID_MEDIA_TYPE",
                field="fileType",
                details={"allowedTypes": ["image/jpeg", "image/png", "image/webp", "video/mp4"]},
            )

        type_config = ALLOWED_TYPES[file_type]

        if file_size > type_config["max_size"]:
            max_mb = type_config["max_size"] / (1024 * 1024)
            raise MediaError(
                f"File size exceeds maximum of {max_mb:.0f} MB",
                code="MEDIA_TOO_LARGE",
                field="fileSize",
                details={"maxSize": type_config["max_size"], "receivedSize": file_size},
            )

        if file_size <= 0:
            raise MediaError(
                "File size must be greater than 0",
                code="INVALID_FILE_SIZE",
            )

        media_id = str(uuid.uuid4())
        ext = type_config["ext"]
        media_type = type_config["media_type"]

        if media_type == MediaType.VIDEO:
            storage_path = f"uploads/{user_id}/videos/{media_id}.{ext}"
        else:
            storage_path = f"uploads/{user_id}/images/{media_id}.{ext}"

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
            logger.error(
                "Failed to generate signed upload URL",
                extra={"storage_path": storage_path, "file_type": file_type},
                exc_info=True,
            )
            raise MediaError(
                "Could not prepare a secure upload URL. Please try again.",
                code="UPLOAD_URL_GENERATION_FAILED",
            ) from e

        media_file = MediaFile.objects.create(
            id=media_id,
            user_id=user_id,
            file_name=file_name,
            file_type=file_type,
            file_size=file_size,
            media_type=media_type,
            storage_path=storage_path,
            status=MediaStatus.PENDING,
        )
        media_url = normalize_url(
            f"https://storage.googleapis.com/{settings.GCP_STORAGE_BUCKET}/{storage_path}"
        )

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
            "media_url": media_url,
            "expires_in": expiry,
        }

    @staticmethod
    def upload_media(user_id: str, file: Any, media_type: str) -> MediaFile:
        """Process an uploaded file and create a MediaFile record.

        Args:
            user_id: ID of the uploader.
            file: The uploaded file object (e.g., from Strawberry Upload).
            media_type: 'IMAGE' or 'VIDEO'.

        Returns:
            Created MediaFile instance.
        """
        from django.core.files.storage import default_storage

        from core.media.services import extract_dimensions

        # 1. Basic Validation
        content_type = getattr(file, "content_type", "")
        if content_type not in ALLOWED_TYPES:
            raise MediaError(
                f"Unsupported file type: {content_type}", code="INVALID_MEDIA_TYPE", field="file"
            )

        limit = ALLOWED_TYPES[content_type]["max_size"]
        if file.size > limit:
            raise MediaError(
                f"File too large. Limit is {limit // (1024*1024)}MB",
                code="MEDIA_TOO_LARGE",
                field="file",
            )

        # 2. Save File Temporarily or to Storage
        ext = ALLOWED_TYPES[content_type]["ext"]
        media_id = str(uuid.uuid4())
        storage_path = f"uploads/{user_id}/{media_type.lower()}s/{media_id}.{ext}"

        # Save File Temporarily to local storage to extract dimensions
        actual_path = default_storage.save(storage_path, file)

        # -- Upload to GCS: this must succeed for the response to be truthful.
        # If GCS is misconfigured, we raise MediaError so the caller receives a
        # typed error — never a false success with an unreachable URL.
        try:
            import os

            from google.cloud import storage as gcs_storage

            full_local_path = default_storage.path(actual_path)
            credentials_file = settings.GCP_CREDENTIALS_FILE

            if credentials_file and os.path.exists(credentials_file):
                gcs_client = gcs_storage.Client.from_service_account_json(credentials_file)
            else:
                gcs_client = gcs_storage.Client()  # Application Default Credentials

            bucket_obj = gcs_client.bucket(settings.GCP_STORAGE_BUCKET)
            blob = bucket_obj.blob(storage_path)

            with open(full_local_path, "rb") as f:
                blob.upload_from_file(f, content_type=content_type)

            logger.info(
                "gcs_upload_success",
                extra={"storage_path": storage_path, "user_id": str(user_id)},
            )

        except Exception as e:
            logger.error(
                "gcs_upload_failed",
                extra={"storage_path": storage_path, "error": str(e)},
                exc_info=True,
            )
            # Clean up the orphaned local file — no point keeping it if GCS failed
            import contextlib

            with contextlib.suppress(Exception):
                default_storage.delete(actual_path)
            raise MediaError(
                "Failed to persist file to storage. Please try again.",
                code="GCS_UPLOAD_FAILED",
            ) from e
        # 3. Extract metadata
        # We need the local path for ffprobe/PIL if not on cloud
        try:
            full_path = default_storage.path(actual_path)
            width, height = extract_dimensions(full_path, media_type)
        except (NotImplementedError, Exception):
            # Fallback if path() isn't available (e.g. S3/GCS without local mirror)
            width, height = 0, 0

        media_file = MediaFile.objects.create(
            user_id=user_id,
            file_name=getattr(file, "name", "upload"),
            file_type=content_type,
            file_size=file.size,
            media_type=media_type.lower(),
            storage_path=actual_path,
            status=MediaStatus.PROCESSING,
            width=width,
            height=height,
        )

        _queue_media_processing(media_file)
        return media_file

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
            ) from None

        if media_file.status != MediaStatus.PENDING:
            raise MediaError(
                f"Media file is already {media_file.status}",
                code="INVALID_STATUS",
            ) from None

        media_file.status = MediaStatus.PROCESSING
        media_file.save(update_fields=["status", "updated_at"])

        try:
            from core.media.tasks import process_media_upload

            process_media_upload.delay(str(media_file.id))
        except Exception as e:
            media_file.status = MediaStatus.FAILED
            media_file.save(update_fields=["status", "updated_at"])
            logger.warning("Failed to queue media processing task: %s", e)
            raise MediaError(
                "Upload completed, but media processing could not be queued. Please retry.",
                code="MEDIA_PROCESSING_QUEUE_FAILED",
            ) from e

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
            ) from None

        if media_file.status != MediaStatus.READY:
            raise MediaError(
                "Media file is not ready for download",
                code="MEDIA_NOT_READY",
            ) from None

        try:
            return _generate_gcp_signed_url(
                bucket=settings.GCP_STORAGE_BUCKET,
                blob_path=media_file.storage_path,
                content_type=media_file.file_type,
                expiry_seconds=3600,
                method="GET",
            )
        except Exception as e:
            logger.error(f"Failed to generate download URL: {e}")
            return normalize_url(
                f"https://storage.googleapis.com/{settings.GCP_STORAGE_BUCKET}/{media_file.storage_path}"
            )


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

    return blob.generate_signed_url(
        version="v4",
        expiration=timedelta(seconds=expiry_seconds),
        method=method,
        content_type=content_type if method == "PUT" else None,
    )


def _queue_media_processing(media_file: MediaFile) -> None:
    """Queue async media optimization and fail visibly if enqueueing is impossible."""
    try:
        from core.media.tasks import process_media_upload

        process_media_upload.delay(str(media_file.id))
    except Exception as exc:
        media_file.status = MediaStatus.FAILED
        media_file.save(update_fields=["status", "updated_at"])
        logger.warning("Failed to queue media processing task: %s", exc)
        raise MediaError(
            "Media uploaded, but processing could not be queued. Please retry.",
            code="MEDIA_PROCESSING_QUEUE_FAILED",
        ) from exc


def extract_dimensions(file_path: str, media_type: str) -> tuple[int, int]:
    """Extract width and height from media file."""
    if media_type.upper() == "IMAGE":
        from PIL import Image

        try:
            with Image.open(file_path) as img:
                return img.width, img.height
        except Exception as e:
            logger.warning(f"Failed to extract image dimensions: {e}")
            return 0, 0

    elif media_type.upper() == "VIDEO":
        import json
        import subprocess

        try:
            cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", file_path]
            output = subprocess.check_output(cmd).decode("utf-8")  # noqa: S603
            data = json.loads(output)
            for stream in data.get("streams", []):
                if stream.get("codec_type") == "video":
                    return int(stream.get("width", 0)), int(stream.get("height", 0))
        except Exception as e:
            logger.warning(f"Failed to extract video dimensions: {e}")
            return 0, 0

    return 0, 0


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
            if len(file_content) >= 8 and signature in file_content[4:8]:
                return True
        elif declared_type == "image/webp":
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

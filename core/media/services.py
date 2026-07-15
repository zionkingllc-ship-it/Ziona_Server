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

from core.media.gcs import (  # noqa: E402,F401
    _generate_gcp_signed_url,
    _get_gcs_bucket,
    _get_gcs_client,
)
from core.media.validators import (  # noqa: E402,F401
    ALLOWED_TYPES,
    ALLOWED_UPLOAD_TYPES,
    IMAGE_MAX_UPLOAD_BYTES,
    MAGIC_BYTES,
    MAX_VIDEO_DURATION_SECONDS,
    MAX_VIDEOS_PER_POST,
    MEGABYTE,
    VIDEO_MAX_UPLOAD_BYTES,
    MediaError,
    _head_external_media_url,
    _host_is_allowed,
    _is_ip_literal,
    build_media_validation_details,
    extract_dimensions,
    validate_magic_bytes,
    validate_trusted_external_image_url,
)


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
                f"File type '{file_type}' not allowed. Accepted: JPEG, PNG, WEBP, MP4, MOV",
                code="INVALID_MEDIA_TYPE",
                field="fileType",
                details=build_media_validation_details(),
            )

        type_config = ALLOWED_TYPES[file_type]

        if file_size > type_config["max_size"]:
            max_mb = type_config["max_size"] / (1024 * 1024)
            raise MediaError(
                f"File size exceeds maximum of {max_mb:.0f} MB",
                code="MEDIA_TOO_LARGE",
                field="fileSize",
                details=build_media_validation_details(
                    max_size=type_config["max_size"],
                    received_size=file_size,
                ),
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

        # 1. Basic Validation
        content_type = getattr(file, "content_type", "")
        if content_type not in ALLOWED_TYPES:
            raise MediaError(
                f"Unsupported file type: {content_type}",
                code="INVALID_MEDIA_TYPE",
                field="file",
                details=build_media_validation_details(),
            )

        limit = ALLOWED_TYPES[content_type]["max_size"]
        if file.size > limit:
            raise MediaError(
                f"File too large. Limit is {limit // (1024 * 1024)}MB",
                code="MEDIA_TOO_LARGE",
                field="file",
                details=build_media_validation_details(max_size=limit, received_size=file.size),
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

        try:
            verified_content_type, verified_size = _verify_uploaded_object(media_file)
        except MediaError as exc:
            _mark_media_failed(media_file, reason=exc.code)
            _delete_uploaded_object(media_file.storage_path)
            raise

        media_file.file_type = verified_content_type
        media_file.file_size = verified_size
        media_file.media_type = ALLOWED_TYPES[verified_content_type]["media_type"]
        media_file.status = MediaStatus.PROCESSING
        media_file.save(
            update_fields=["file_type", "file_size", "media_type", "status", "updated_at"]
        )

        _queue_media_processing(media_file)

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


def _queue_media_processing(media_file: MediaFile) -> None:
    """Queue async media optimization and fail visibly if enqueueing is impossible."""
    try:
        from core.media.tasks import process_media_upload

        process_media_upload.apply_async(
            args=[str(media_file.id)],
            queue=settings.CELERY_QUEUE_MEDIA,
            priority=settings.CELERY_MEDIA_TASK_PRIORITY,
        )
    except Exception as exc:
        _mark_media_failed(media_file, reason="MEDIA_PROCESSING_QUEUE_FAILED")
        logger.warning("Failed to queue media processing task: %s", exc)
        raise MediaError(
            "Media uploaded, but processing could not be queued. Please retry.",
            code="MEDIA_PROCESSING_QUEUE_FAILED",
        ) from exc


def _verify_uploaded_object(media_file: MediaFile) -> tuple[str, int]:
    """Validate a client-uploaded GCS object before the backend trusts it."""
    expected_type = (media_file.file_type or "").split(";", 1)[0].strip().lower()
    type_config = ALLOWED_TYPES.get(expected_type)
    if not type_config:
        raise MediaError(
            "Uploaded file type is not allowed.",
            code="INVALID_MEDIA_TYPE",
            details=build_media_validation_details(),
        )

    bucket = _get_gcs_bucket()
    blob = bucket.blob(media_file.storage_path)
    try:
        blob.reload()
    except Exception as exc:  # noqa: BLE001
        raise MediaError(
            "Uploaded media object was not found.",
            code="MEDIA_OBJECT_NOT_FOUND",
        ) from exc

    actual_size = int(blob.size or 0)
    if actual_size <= 0:
        raise MediaError(
            "Uploaded file is empty.",
            code="INVALID_FILE_SIZE",
            details=build_media_validation_details(
                max_size=type_config["max_size"],
                received_size=actual_size,
            ),
        )
    if actual_size > type_config["max_size"]:
        raise MediaError(
            "Uploaded file exceeds the allowed size.",
            code="MEDIA_TOO_LARGE",
            details=build_media_validation_details(
                max_size=type_config["max_size"],
                received_size=actual_size,
            ),
        )

    actual_type = (blob.content_type or expected_type).split(";", 1)[0].strip().lower()
    actual_config = ALLOWED_TYPES.get(actual_type)
    if not actual_config:
        raise MediaError(
            "Uploaded file type is not allowed.",
            code="INVALID_MEDIA_TYPE",
            details=build_media_validation_details(),
        )
    if actual_config["media_type"] != type_config["media_type"]:
        raise MediaError(
            "Uploaded file type does not match the declared media type.",
            code="INVALID_MEDIA_TYPE",
            details=build_media_validation_details(),
        )

    file_head = blob.download_as_bytes(start=0, end=31)
    if not validate_magic_bytes(file_head, actual_type):
        raise MediaError(
            "Uploaded file contents do not match its declared type.",
            code="INVALID_MEDIA_SIGNATURE",
            details=build_media_validation_details(),
        )

    return actual_type, actual_size


def _delete_uploaded_object(storage_path: str) -> None:
    if not storage_path:
        return
    try:
        _get_gcs_bucket().blob(storage_path).delete()
    except Exception:  # noqa: BLE001
        logger.warning(
            "failed_to_delete_invalid_media_object", extra={"storage_path": storage_path}
        )


def _mark_media_failed(media_file: MediaFile, reason: str | None = None) -> None:
    media_file.status = MediaStatus.FAILED
    media_file.processing_error_code = reason or "MEDIA_PROCESSING_FAILED"
    media_file.processing_error_message = _media_failure_message(reason)
    media_file.processing_failed_stage = "upload_confirm"
    media_file.save(
        update_fields=[
            "status",
            "processing_error_code",
            "processing_error_message",
            "processing_failed_stage",
            "updated_at",
        ]
    )
    logger.warning(
        "media_marked_failed",
        extra={"media_id": str(media_file.id), "reason": reason},
    )


def _media_failure_message(reason: str | None) -> str:
    messages = {
        "MEDIA_PROCESSING_QUEUE_FAILED": "Media uploaded, but processing could not be queued. Please retry.",
        "MEDIA_OBJECT_NOT_FOUND": "Uploaded media object was not found. Please upload the file again.",
        "MEDIA_TOO_LARGE": "Uploaded file exceeds the allowed size.",
        "INVALID_FILE_SIZE": "Uploaded file is empty or invalid.",
        "INVALID_MEDIA_TYPE": "Uploaded file type is not allowed.",
        "INVALID_MEDIA_SIGNATURE": "Uploaded file contents do not match its declared type.",
    }
    return messages.get(reason or "", "Media validation failed before processing.")

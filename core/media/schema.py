"""GraphQL types and mutations for the media domain."""

import logging

import strawberry
from strawberry.file_uploads import Upload

from core.shared.types import ErrorType, MediaType

logger = logging.getLogger("core.media")


@strawberry.type
class MediaFileType:
    id: str
    url: str
    type: MediaType  # ENUM (IMAGE or VIDEO)
    width: int | None = None
    height: int | None = None
    thumbnail_url: str | None = None  # For videos
    duration: int | None = None  # For videos


@strawberry.type
class MediaUploadPayload:
    """Response type for media upload mutations."""

    success: bool
    upload_url: str | None = None  # For signed URL approach
    media_id: str | None = None
    media_url: str | None = None
    status: str | None = None
    expires_in: int | None = None
    error: ErrorType | None = None


@strawberry.type
class MediaStatusPayload:
    """Response type for polling media processing state."""

    success: bool
    media_id: str | None = None
    media_url: str | None = None
    thumbnail_url: str | None = None
    status: str | None = None
    error: ErrorType | None = None


@strawberry.type
class MediaQueries:
    """Media status queries."""

    @strawberry.field(description="Return media processing status by media ID")
    def media_status(
        self,
        info: strawberry.types.Info,
        media_id: str,
    ) -> MediaStatusPayload:
        from core.media.models import MediaFile
        from core.users.schema import _get_authenticated_user_id

        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return MediaStatusPayload(
                success=False,
                error=ErrorType(code="UNAUTHORIZED", message="Authentication required"),
            )

        media_file = MediaFile.objects.filter(id=media_id, user_id=user_id).first()
        if not media_file:
            return MediaStatusPayload(
                success=False,
                error=ErrorType(code="MEDIA_NOT_FOUND", message="Media file not found"),
            )

        return MediaStatusPayload(
            success=True,
            media_id=str(media_file.id),
            media_url=media_file.url,
            thumbnail_url=media_file.thumbnail_url,
            status=media_file.status,
        )


@strawberry.type
class MediaMutations:
    """Media domain mutations."""

    @strawberry.mutation(description="Directly upload media file (image or video) seamlessly")
    def direct_upload_media(
        self,
        info: strawberry.types.Info,
        file: Upload,
        media_type: MediaType,
    ) -> MediaUploadPayload:
        """
        Upload media file (image or video).

        **Process:**
        1. Validate file type and size
        2. Extract dimensions from file
        3. Save file to storage
        4. Return mediaId and mediaUrl

        **Use mediaId in createPost mutation.**
        """
        from core.media.services import MediaError, MediaService
        from core.users.schema import _get_authenticated_user_id

        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return MediaUploadPayload(
                success=False,
                error=ErrorType(code="UNAUTHORIZED", message="Authentication required"),
            )

        logger.info("direct_upload user_id=%s media_type=%s", user_id, media_type.value)
        try:
            # Service handles validation and upload
            media_file = MediaService.upload_media(
                user_id=user_id,
                file=file,
                media_type=media_type.value,
            )

            return MediaUploadPayload(
                success=True,
                media_id=str(media_file.id),
                media_url=media_file.url,
                status=media_file.status,
            )

        except (MediaError, ValueError) as e:
            code = getattr(e, "code", "VALIDATION_ERROR")
            message = getattr(e, "message", str(e))
            logger.warning("direct_upload_failed user_id=%s code=%s", user_id, code)
            field = getattr(e, "field", "file")
            details = getattr(e, "details", None)
            return MediaUploadPayload(
                success=False,
                error=ErrorType(code=code, message=message, field=field, details=details),
            )

    @strawberry.mutation(
        description="Request a signed URL for media upload correctly matching uploadMedia mapping"
    )
    def upload_media(
        self,
        info: strawberry.types.Info,
        file_name: str,
        file_type: str,
        file_size: int,
    ) -> MediaUploadPayload:
        """Generate a signed URL for direct file upload to GCP."""
        from core.media.services import MediaError, MediaService
        from core.users.schema import _get_authenticated_user_id

        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return MediaUploadPayload(
                success=False,
                error=ErrorType(code="UNAUTHORIZED", message="Authentication required"),
            )

        logger.info("upload_media user_id=%s file=%s size=%d", user_id, file_name, file_size)
        try:
            result = MediaService.generate_upload_url(
                user_id=user_id,
                file_name=file_name,
                file_type=file_type,
                file_size=file_size,
            )
            return MediaUploadPayload(
                success=True,
                upload_url=result["upload_url"],
                media_id=result["media_id"],
                media_url=result["media_url"],
                status="pending",
                expires_in=result["expires_in"],
            )
        except MediaError as e:
            code = getattr(e, "code", "VALIDATION_ERROR")
            message = getattr(e, "message", str(e))
            logger.warning(
                "upload_media_failed user_id=%s code=%s file=%s", user_id, code, file_name
            )
            field = getattr(e, "field", "file")
            details = getattr(e, "details", None)
            return MediaUploadPayload(
                success=False,
                error=ErrorType(code=code, message=message, field=field, details=details),
            )

    @strawberry.mutation(description="Confirm direct-to-GCS media upload and start processing")
    def confirm_media_upload(
        self,
        info: strawberry.types.Info,
        media_id: str,
    ) -> MediaUploadPayload:
        """Mark a signed-url upload complete and enqueue optimization."""
        from core.media.services import MediaError, MediaService
        from core.users.schema import _get_authenticated_user_id

        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return MediaUploadPayload(
                success=False,
                error=ErrorType(code="UNAUTHORIZED", message="Authentication required"),
            )

        try:
            media_file = MediaService.confirm_upload(media_id=media_id, user_id=user_id)
            return MediaUploadPayload(
                success=True,
                media_id=str(media_file.id),
                media_url=media_file.url,
                status=media_file.status,
            )
        except MediaError as e:
            return MediaUploadPayload(
                success=False,
                media_id=media_id,
                error=ErrorType(
                    code=getattr(e, "code", "VALIDATION_ERROR"),
                    message=getattr(e, "message", str(e)),
                    field=getattr(e, "field", None),
                    details=getattr(e, "details", None),
                ),
            )

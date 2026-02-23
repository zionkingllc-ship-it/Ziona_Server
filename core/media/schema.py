"""GraphQL types and mutations for the media domain."""

import strawberry

# --- Types ---


@strawberry.type
class MediaUploadPayload:
    """Response type for media upload mutations."""

    success: bool
    upload_url: str | None = None
    media_id: str | None = None
    expires_in: int | None = None
    message: str | None = None
    error_code: str | None = None


# --- Mutations ---


@strawberry.type
class MediaMutations:
    """Media domain mutations."""

    @strawberry.mutation(description="Request a signed URL for media upload")
    def request_media_upload(
        self,
        info: strawberry.types.Info,
        file_name: str,
        file_type: str,
        file_size: int,
    ) -> MediaUploadPayload:
        """Generate a signed URL for direct file upload to GCP."""
        from core.media.services import MediaError, MediaService

        # Check auth
        request = info.context["request"]
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        if not auth_header.startswith("Bearer "):
            return MediaUploadPayload(
                success=False,
                message="Authentication required",
                error_code="UNAUTHORIZED",
            )

        try:
            from core.authentication.tokens import TokenService

            payload = TokenService.validate_access_token(auth_header[7:])
            user_id = payload["user_id"]
        except Exception:
            return MediaUploadPayload(
                success=False,
                message="Invalid access token",
                error_code="UNAUTHORIZED",
            )

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
                expires_in=result["expires_in"],
            )
        except MediaError as e:
            return MediaUploadPayload(
                success=False,
                message=e.message,
                error_code=e.code,
            )

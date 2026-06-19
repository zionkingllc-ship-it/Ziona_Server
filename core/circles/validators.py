"""
Validators for Circle Responses.
- Media trust and type validation.
- Content validations.
"""

from core.media.services import MediaError, validate_trusted_external_image_url
from core.shared.exceptions import ZionaError


def validate_response_media(media_type: str, media_url: str) -> str:
    """
    Validates media attached to a response.
    External video URLs are intentionally rejected to avoid SSRF and remote probing.
    External images must be trusted HTTPS hosts from MEDIA_URL_ALLOWLIST.
    """
    if not media_type or not media_url:
        return media_url

    if media_type not in ["image", "video"]:
        raise ZionaError(message="Media type must be image or video", code="INVALID_MEDIA_TYPE")

    if media_type == "video":
        raise ZionaError(
            message="Externally hosted videos are not accepted. Use signed media upload.",
            code="EXTERNAL_VIDEO_NOT_ALLOWED",
        )

    try:
        return validate_trusted_external_image_url(media_url)
    except MediaError as exc:
        raise ZionaError(
            message=exc.message,
            code=exc.code,
            extensions=exc.details,
        ) from exc

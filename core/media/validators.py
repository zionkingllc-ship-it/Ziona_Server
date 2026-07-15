"""Media validation — magic bytes, trusted-host SSRF checks, dimensions.

Split from core/media/services.py (no behavior change). These are the symbols
imported cross-app (circles/posts validators); core.media.services re-exports
them so existing import paths keep working.
"""

import ipaddress
import logging
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from django.conf import settings

from core.media.models import MediaType
from core.shared.utils import normalize_url

logger = logging.getLogger("core.media")

MEGABYTE = 1024 * 1024
IMAGE_MAX_UPLOAD_BYTES = 10 * MEGABYTE
VIDEO_MAX_UPLOAD_BYTES = 100 * MEGABYTE
MAX_VIDEO_DURATION_SECONDS = 90
MAX_VIDEOS_PER_POST = 1
ALLOWED_UPLOAD_TYPES = [
    "image/jpeg",
    "image/png",
    "image/jpg",
    "image/webp",
    "video/mp4",
    "video/quicktime",
]

ALLOWED_TYPES = {
    "image/jpeg": {"ext": "jpg", "media_type": MediaType.IMAGE, "max_size": IMAGE_MAX_UPLOAD_BYTES},
    "image/png": {"ext": "png", "media_type": MediaType.IMAGE, "max_size": IMAGE_MAX_UPLOAD_BYTES},
    "image/jpg": {"ext": "jpg", "media_type": MediaType.IMAGE, "max_size": IMAGE_MAX_UPLOAD_BYTES},
    "image/webp": {
        "ext": "webp",
        "media_type": MediaType.IMAGE,
        "max_size": IMAGE_MAX_UPLOAD_BYTES,
    },
    "video/mp4": {"ext": "mp4", "media_type": MediaType.VIDEO, "max_size": VIDEO_MAX_UPLOAD_BYTES},
    "video/quicktime": {
        "ext": "mov",
        "media_type": MediaType.VIDEO,
        "max_size": VIDEO_MAX_UPLOAD_BYTES,
    },
}


MAGIC_BYTES = {
    "image/jpeg": [b"\xff\xd8\xff"],
    "image/jpg": [b"\xff\xd8\xff"],
    "image/png": [b"\x89PNG\r\n\x1a\n"],
    "image/webp": [b"RIFF"],
    "video/mp4": [b"ftyp"],
    "video/quicktime": [b"ftyp"],
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


def validate_trusted_external_image_url(url: str) -> str:
    """Return a normalized external image URL if it passes host and redirect validation."""
    normalized_url = normalize_url(url)
    current_url = normalized_url

    for _ in range(4):
        parsed = urlparse(current_url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https":
            raise MediaError(
                "Only HTTPS media URLs are allowed.",
                code="INVALID_MEDIA_URL",
                details={"url": normalized_url},
            )
        if not host or host == "localhost" or _is_ip_literal(host):
            raise MediaError(
                "Media URLs must use a trusted public host.",
                code="INVALID_MEDIA_URL",
                details={"url": normalized_url},
            )
        if not _host_is_allowed(host):
            raise MediaError(
                "Media host is not allowlisted.",
                code="INVALID_MEDIA_URL",
                details={"url": normalized_url, "host": host},
            )

        response = _head_external_media_url(current_url)
        if response.is_redirect or response.is_permanent_redirect:
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise MediaError(
                    "Media URL redirect is missing a target location.",
                    code="INVALID_MEDIA_URL",
                    details={"url": normalized_url},
                )
            current_url = normalize_url(urljoin(current_url, location))
            continue

        content_type = (response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        response.close()
        if not content_type.startswith("image/"):
            raise MediaError(
                "Only externally hosted images are accepted.",
                code="INVALID_MEDIA_URL",
                details={"url": normalized_url, "contentType": content_type or None},
            )
        return current_url

    raise MediaError(
        "Media URL has too many redirects.",
        code="INVALID_MEDIA_URL",
        details={"url": normalized_url},
    )


def _head_external_media_url(url: str) -> requests.Response:
    try:
        response = requests.head(url, allow_redirects=False, timeout=5)
        if response.status_code == 405:
            response.close()
            response = requests.get(url, allow_redirects=False, timeout=5, stream=True)
        return response
    except requests.RequestException as exc:
        raise MediaError(
            "Could not validate the external media URL.",
            code="INVALID_MEDIA_URL",
            details={"url": url},
        ) from exc


def _host_is_allowed(host: str) -> bool:
    allowlist = getattr(settings, "MEDIA_URL_ALLOWLIST", [])
    normalized_allowlist = [entry.strip().lower() for entry in allowlist if entry and entry.strip()]
    return any(host == allowed or host.endswith(f".{allowed}") for allowed in normalized_allowlist)


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def build_media_validation_details(
    *,
    allowed_types: list[str] | None = None,
    max_size: int | None = None,
    received_size: int | None = None,
    received_duration_seconds: float | int | None = None,
    received_videos_count: int | None = None,
) -> dict[str, Any]:
    """Build a consistent validation details payload for upload/post media errors."""
    details: dict[str, Any] = {
        "allowedTypes": allowed_types or list(ALLOWED_UPLOAD_TYPES),
        "maxVideoSizeBytes": VIDEO_MAX_UPLOAD_BYTES,
        "maxVideoDurationSeconds": MAX_VIDEO_DURATION_SECONDS,
        "maxVideosPerPost": MAX_VIDEOS_PER_POST,
    }
    if max_size is not None:
        details["maxSize"] = max_size
    if received_size is not None:
        details["receivedSize"] = received_size
    if received_duration_seconds is not None:
        details["receivedDurationSeconds"] = received_duration_seconds
    if received_videos_count is not None:
        details["receivedVideosCount"] = received_videos_count
    return details

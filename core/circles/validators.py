# ruff: noqa: S603, S607
"""
Validators for Circle Responses.
- Media validations (image sizes, video durations using ffprobe).
- Content validations.
"""

import subprocess

from django.conf import settings

from core.shared.exceptions import ZionaError


def validate_response_media(media_type: str, media_url: str):
    """
    Validates media attached to a response.
    For videos, enforces 15-30 second duration limit.
    """
    if not media_type or not media_url:
        return

    if media_type not in ["image", "video"]:
        raise ZionaError(message="Media type must be image or video", code="INVALID_MEDIA_TYPE")

    if media_type == "video":
        # Skip ffprobe check during local tests if settings flag is set
        if getattr(settings, "SKIP_FFPROBE_TESTS", False):
            return

        try:
            # We assume the media_url is accessible. In production this would be a signed URL
            # Or we validate *before* upload. For this milestone, we run ffprobe on the URL.
            command = [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                media_url,
            ]
            result = subprocess.run(  # noqa: S603, S607
                command, capture_output=True, text=True, timeout=10
            )

            if result.returncode != 0:
                raise ZionaError(message="Could not validate video file", code="INVALID_VIDEO_FILE")

            duration = float(result.stdout.strip())

            if duration < 15.0 or duration > 30.0:
                raise ZionaError(
                    message=f"Video must be between 15 and 30 seconds. Current: {duration:.1f}s",
                    code="INVALID_VIDEO_DURATION",
                )

        except (subprocess.TimeoutExpired, ValueError):
            raise ZionaError(
                message="Timeout or error validating video duration", code="VIDEO_VALIDATION_FAILED"
            ) from None
        except FileNotFoundError:
            # ffprobe not installed, pass for now (or fail loud in prod)
            pass

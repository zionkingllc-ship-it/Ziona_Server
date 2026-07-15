"""Pure media file processing — ffmpeg/PIL work, no Celery, no GCS.

Split from core/media/tasks.py (no behavior change). The Celery task shells
remain in core.media.tasks (task names are registered by module path) and
re-export these helpers, keeping existing imports and patch targets valid.
"""

import logging
import re
import subprocess
from pathlib import Path

from billiard.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from PIL import UnidentifiedImageError

from core.media.models import MediaFile, MediaType

logger = logging.getLogger("core.media")


def _optimize_image_file(
    input_path: Path,
    output_path: Path,
    content_type: str,
) -> tuple[str, int, int]:
    """Resize and compress an image file, returning content type and dimensions."""
    from PIL import Image, ImageOps

    with Image.open(input_path) as image:
        image = ImageOps.exif_transpose(image)
        image.thumbnail(
            (settings.MEDIA_IMAGE_MAX_DIMENSION, settings.MEDIA_IMAGE_MAX_DIMENSION),
            Image.Resampling.LANCZOS,
        )
        width, height = image.size

        if content_type in {"image/jpeg", "image/jpg"}:
            optimized = image.convert("RGB")
            optimized.save(
                output_path,
                format="JPEG",
                quality=settings.MEDIA_IMAGE_JPEG_QUALITY,
                optimize=True,
                progressive=True,
            )
            return "image/jpeg", width, height

        if content_type == "image/webp":
            optimized = image.convert("RGBA" if _has_alpha(image) else "RGB")
            optimized.save(
                output_path,
                format="WEBP",
                quality=settings.MEDIA_IMAGE_JPEG_QUALITY,
                method=6,
            )
            return "image/webp", width, height

        optimized = image.convert("RGBA" if _has_alpha(image) else "RGB")
        optimized.save(output_path, format="PNG", optimize=True, compress_level=9)
        return "image/png", width, height


def _optimize_video_file(input_path: Path, output_path: Path) -> None:
    """Normalize video into a cost-conscious MP4 delivery profile."""
    import imageio_ffmpeg

    ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
    scale_filter = (
        "scale="
        f"if(gt(iw\\,ih)\\,min({settings.MEDIA_VIDEO_MAX_DIMENSION}\\,iw)\\,-2):"
        f"if(gt(iw\\,ih)\\,-2\\,min({settings.MEDIA_VIDEO_MAX_DIMENSION}\\,ih))"
    )
    cmd = [
        ffmpeg_bin,
        "-y",
        "-i",
        str(input_path),
        "-map_metadata",
        "-1",
        "-vf",
        scale_filter,
        "-c:v",
        "libx264",
        "-preset",
        settings.MEDIA_VIDEO_PRESET,
        "-crf",
        str(settings.MEDIA_VIDEO_CRF),
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-threads",
        "1",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        str(output_path),
    ]
    result = subprocess.run(
        cmd,  # noqa: S603
        capture_output=True,
        timeout=settings.MEDIA_VIDEO_OPTIMIZE_TIMEOUT_SECONDS,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"FFmpeg video optimization failed: {stderr[:500]}")


def _extract_video_metadata(video_path: Path) -> tuple[int | None, int | None, float | None]:
    """Extract normalized video width, height, and duration using bundled FFmpeg."""
    import imageio_ffmpeg

    ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
    result = subprocess.run(
        [ffmpeg_bin, "-i", str(video_path)],  # noqa: S603
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    stderr = result.stderr or ""

    width = height = None
    duration = None

    duration_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", stderr)
    if duration_match:
        hours = int(duration_match.group(1))
        minutes = int(duration_match.group(2))
        seconds = float(duration_match.group(3))
        duration = round((hours * 3600) + (minutes * 60) + seconds, 3)

    for line in stderr.splitlines():
        if "Video:" not in line:
            continue
        dimensions_match = re.search(r"(\d{2,5})x(\d{2,5})", line)
        if dimensions_match:
            width = int(dimensions_match.group(1))
            height = int(dimensions_match.group(2))
            break

    return width, height, duration


def _has_alpha(image) -> bool:
    if image.mode in {"RGBA", "LA"}:
        return True
    return image.mode == "P" and "transparency" in image.info


def _generate_video_thumbnail_file(input_path: Path, output_path: Path) -> None:
    """Generate a thumbnail image from a local optimized video file."""
    import imageio_ffmpeg

    ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        ffmpeg_bin,
        "-y",
        "-ss",
        "1",
        "-i",
        str(input_path),
        "-vframes",
        "1",
        "-vf",
        "crop=min(iw\\,ih):min(iw\\,ih),scale=640:640",
        "-f",
        "image2",
        "-vcodec",
        "mjpeg",
        "-loglevel",
        "error",
        "-threads",
        "1",
        str(output_path),
    ]
    result = subprocess.run(
        cmd,  # noqa: S603
        capture_output=True,
        timeout=settings.MEDIA_THUMBNAIL_TIMEOUT_SECONDS,
        check=False,
    )

    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"FFmpeg exited with code {result.returncode}: {stderr[:500]}")
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError("FFmpeg produced an empty thumbnail image")


def get_ffmpeg_runtime_info() -> dict[str, str | None]:
    """Return the resolved FFmpeg binary path and version banner."""
    import imageio_ffmpeg

    ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [ffmpeg_bin, "-version"]
    version_result = subprocess.run(
        cmd,  # noqa: S603
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    version_line = None
    if version_result.stdout:
        version_line = version_result.stdout.splitlines()[0].strip()
    return {"path": ffmpeg_bin, "version": version_line}


def _classify_processing_failure(media_file: MediaFile, exc: Exception) -> tuple[str, str]:
    """Return a stable client-safe code/message for a media processing failure."""
    media_kind = "Video" if media_file.media_type == MediaType.VIDEO else "Media"
    error_text = str(exc)
    lowered_error = error_text.lower()

    if isinstance(exc, SoftTimeLimitExceeded):
        return (
            "VIDEO_PROCESSING_TIMEOUT"
            if media_file.media_type == MediaType.VIDEO
            else "MEDIA_PROCESSING_TIMEOUT",
            f"{media_kind} processing exceeded the worker time limit. Please try a shorter or lower-resolution file.",
        )

    if isinstance(exc, subprocess.TimeoutExpired):
        return (
            "VIDEO_PROCESSING_TIMEOUT"
            if media_file.media_type == MediaType.VIDEO
            else "MEDIA_PROCESSING_TIMEOUT",
            f"{media_kind} processing timed out. Please try a shorter or lower-resolution file.",
        )

    if media_file.media_type == MediaType.VIDEO and (
        "code -11" in lowered_error
        or "sigsegv" in lowered_error
        or "signal 11" in lowered_error
        or "out of memory" in lowered_error
        or "oom" in lowered_error
    ):
        return (
            "VIDEO_PROCESSING_RESOURCE_LIMIT",
            "Video processing exceeded available server resources. Please try a shorter or lower-resolution video.",
        )

    if isinstance(exc, UnidentifiedImageError):
        return (
            "IMAGE_PROCESSING_FAILED",
            "Image processing failed. Please upload a valid image file.",
        )

    if media_file.media_type == MediaType.VIDEO and "ffmpeg" in lowered_error:
        return (
            "VIDEO_PROCESSING_FAILED",
            "Video processing failed. Please try a different video format.",
        )

    return (
        "MEDIA_PROCESSING_FAILED",
        f"{media_kind} processing failed. Please try another file.",
    )

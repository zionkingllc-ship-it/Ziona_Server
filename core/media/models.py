"""
Media models for file upload tracking.

Tracks uploaded media files, their processing status,
and metadata. Files are stored in GCP Cloud Storage.
"""

from django.conf import settings
from django.db import models

from core.shared.models import TimestampedModel
from core.shared.utils import normalize_url


class MediaStatus(models.TextChoices):
    """Processing status for uploaded media."""

    PENDING = "pending", "Pending Upload"
    PROCESSING = "processing", "Processing"
    READY = "ready", "Ready"
    FAILED = "failed", "Failed"


class MediaType(models.TextChoices):
    """Supported media file types."""

    IMAGE = "image", "Image"
    VIDEO = "video", "Video"


class MediaFile(TimestampedModel):
    """Tracks an uploaded media file.

    Attributes:
        user: Foreign key to the uploading user.
        file_name: Original file name.
        file_type: MIME type of the file.
        file_size: File size in bytes.
        media_type: image or video.
        storage_path: Path in GCP Cloud Storage bucket.
        thumbnail_path: Path to generated thumbnail (videos).
        status: Processing status.
        width: Image/video width in pixels.
        height: Image/video height in pixels.
        duration: Video duration in seconds.
    """

    user = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="media_files",
    )
    file_name = models.CharField(max_length=255)
    file_type = models.CharField(max_length=50)
    file_size = models.BigIntegerField(help_text="File size in bytes")
    media_type = models.CharField(
        max_length=10,
        choices=MediaType.choices,
        db_index=True,
    )
    storage_path = models.CharField(max_length=500, blank=True)
    thumbnail_path = models.CharField(max_length=500, blank=True)
    status = models.CharField(
        max_length=20,
        choices=MediaStatus.choices,
        default=MediaStatus.PENDING,
        db_index=True,
    )

    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    duration = models.FloatField(null=True, blank=True, help_text="Video duration in seconds")

    class Meta:
        db_table = "media_files"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "status"], name="idx_media_user_status"),
        ]

    @property
    def url(self) -> str:
        """Return public URL for the media file."""
        if not self.storage_path:
            return ""

        return normalize_url(
            f"https://storage.googleapis.com/{settings.GCP_STORAGE_BUCKET}/{self.storage_path}"
        )

    def __str__(self) -> str:
        """Return string representation."""
        return f"{self.file_name} ({self.status})"

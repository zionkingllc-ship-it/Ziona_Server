import uuid

from django.db import models
from django.utils import timezone


class TimestampedModel(models.Model):
    """Abstract model with UUID primary key and audit timestamps.

    All domain models should inherit from this to get consistent
    UUID PKs and created_at/updated_at timestamps.

    Attributes:
        id: UUID primary key, auto-generated.
        created_at: Timestamp set on creation, never modified.
        updated_at: Timestamp updated on every save.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ["-created_at"]


class SoftDeleteManager(models.Manager):
    """Manager that excludes soft-deleted records by default."""

    def get_queryset(self):
        """Return only non-deleted records."""
        return super().get_queryset().filter(deleted_at__isnull=True)


class AllObjectsManager(models.Manager):
    """Manager that includes soft-deleted records."""

    pass


class SoftDeleteModel(TimestampedModel):
    """Abstract model with soft delete support.

    Records are never hard-deleted. Instead, deleted_at is set
    to the current timestamp. Use `all_objects` manager to
    include deleted records in queries.

    Attributes:
        deleted_at: Timestamp when record was soft-deleted, null if active.
    """

    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)

    objects = SoftDeleteManager()
    all_objects = AllObjectsManager()

    class Meta:
        abstract = True
        ordering = ["-created_at"]

    def soft_delete(self) -> None:
        """Mark this record as deleted without removing from database."""
        self.deleted_at = timezone.now()
        self.save(update_fields=["deleted_at", "updated_at"])

    def restore(self) -> None:
        """Restore a soft-deleted record."""
        self.deleted_at = None
        self.save(update_fields=["deleted_at", "updated_at"])

    @property
    def is_deleted(self) -> bool:
        """Check if this record has been soft-deleted."""
        return self.deleted_at is not None

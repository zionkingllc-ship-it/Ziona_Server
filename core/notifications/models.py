"""
Notification models for the Ziona platform.

Provides in-app notifications for follows, mentions, replies, likes, and comments.
"""

import uuid

from django.db import models

from core.shared.models import TimestampedModel


class NotificationType(models.TextChoices):
    """Types of notifications a user can receive."""

    FOLLOW = "follow", "Follow"
    FOLLOW_SUGGESTION = "follow_suggestion", "Follow Suggestion"
    MENTION = "mention", "Mention"
    REPLY = "reply", "Reply"
    CIRCLE_POST = "circle_post", "Circle Post"
    LIKE = "like", "Like"
    COMMENT = "comment", "Comment"


class Notification(TimestampedModel):
    """An in-app notification delivered to a user.

    Attributes:
        id: UUID primary key.
        recipient: The user receiving the notification.
        actor: The user who triggered the notification.
        type: The category of the notification.
        message: The text content to display.
        is_read: Whether the user has seen it.
        entity_id: Optional ID of the related object (e.g., post ID, comment ID).
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    recipient = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="notifications_received",
        db_index=True,
    )
    actor = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="notifications_triggered",
    )
    type = models.CharField(
        max_length=50,
        choices=NotificationType.choices,
        db_index=True,
    )
    message = models.CharField(max_length=255)
    is_read = models.BooleanField(default=False, db_index=True)
    entity_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "notifications"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["recipient", "-created_at"], name="idx_notif_recipient_date"),
        ]

    def __str__(self) -> str:
        """Return string representation."""
        return f"{self.type} for {self.recipient_id} by {self.actor_id}"

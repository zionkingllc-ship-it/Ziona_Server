import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.shared.models import TimestampedModel


class NotificationType(models.TextChoices):
    """Types of notifications a user can receive."""

    REPLY_COMMENT = "reply_comment", "Reply to Comment"
    REPLY_POST = "reply_post", "Reply to Post"
    LIKE_POST = "like_post", "Like Post"
    LIKE_COMMENT = "like_comment", "Like Comment"
    NEW_ANCHOR = "new_anchor", "New Anchor"
    MENTION = "mention", "Mention"
    NEW_CIRCLE_POST = "new_circle_post", "New Circle Post"
    ADMIN_ANNOUNCEMENT = "admin_announcement", "Admin Announcement"


class NotificationStatus(models.TextChoices):
    """Status of a notification to support soft-deletion."""

    ACTIVE = "active", "Active"
    DELETED = "deleted", "Deleted"


class Notification(TimestampedModel):
    """An in-app notification delivered to a user."""

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
        db_index=True,
    )
    notification_type = models.CharField(
        max_length=50,
        choices=NotificationType.choices,
        db_index=True,
    )
    reference_id = models.UUIDField(null=True, blank=True)
    reference_type = models.CharField(max_length=50, blank=True)
    message = models.TextField()
    is_read = models.BooleanField(default=False)
    status = models.CharField(
        max_length=20,
        choices=NotificationStatus.choices,
        default=NotificationStatus.ACTIVE,
    )

    class Meta:
        db_table = "notifications"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "is_read", "-created_at"], name="idx_notif_user_read_dt"),
            models.Index(fields=["reference_type", "reference_id"], name="idx_notif_ref"),
        ]

    def __str__(self) -> str:
        return f"{self.notification_type} for {self.user_id}"


class NotificationPreference(TimestampedModel):
    """User preferences for which notifications to receive."""

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_preferences",
    )
    anchor_notifications = models.BooleanField(default=True)
    reply_notifications = models.BooleanField(default=True)
    like_notifications = models.BooleanField(default=True)
    circle_activity_notifications = models.BooleanField(default=True)
    admin_announcements = models.BooleanField(default=True)

    class Meta:
        db_table = "notification_preferences"

    def __str__(self) -> str:
        return f"Preferences for {self.user_id}"


class DeviceToken(TimestampedModel):
    """FCM device tokens for push notifications."""

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="device_tokens",
    )
    token = models.CharField(max_length=255, unique=True)
    platform = models.CharField(max_length=20)  # e.g., 'ios', 'android'
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "device_tokens"
        constraints = [models.UniqueConstraint(fields=["user", "token"], name="unique_user_token")]

    def __str__(self) -> str:
        return f"{self.platform} token for {self.user_id}"


class NotificationMetrics(models.Model):
    """Daily aggregated metrics for notifications."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    date = models.DateField(default=timezone.now)
    notification_type = models.CharField(max_length=50, choices=NotificationType.choices)
    sent_count = models.IntegerField(default=0)
    opened_count = models.IntegerField(default=0)
    user_return_count = models.IntegerField(default=0)

    class Meta:
        db_table = "notification_metrics"
        constraints = [
            models.UniqueConstraint(
                fields=["date", "notification_type"], name="unique_date_type_metrics"
            )
        ]

    def __str__(self) -> str:
        return f"Metrics for {self.notification_type} on {self.date}"

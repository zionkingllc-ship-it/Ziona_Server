"""
Admin Dashboard models — audit logs, moderation actions, analytics, and contact system.

All models use UUID primary keys and follow the project's established patterns.
"""

import uuid

from django.conf import settings
from django.db import models

from core.shared.models import TimestampedModel

# ──────────────────────────────────────────────
#  Audit & Moderation
# ──────────────────────────────────────────────


class AdminAuditLog(models.Model):
    """Immutable record of every admin action for compliance (2-year retention).

    Attributes:
        admin_user: The admin who performed the action.
        action: Machine-readable action code (e.g., USER_SUSPENDED).
        target_type: Entity type affected (User, Circle, Post, etc.).
        target_id: UUID or identifier of the affected entity.
        details: Full before/after state snapshot as JSON.
        ip_address: IP address of the admin at time of action.
        created_at: Immutable timestamp of the action.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    admin_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="admin_audit_logs",
    )
    action = models.CharField(max_length=100, db_index=True)
    target_type = models.CharField(max_length=50)
    target_id = models.CharField(max_length=100)
    details = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "admin_audit_logs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["-created_at"], name="idx_audit_created_desc"),
            models.Index(fields=["admin_user", "-created_at"], name="idx_audit_admin_created"),
            models.Index(fields=["action"], name="idx_audit_action"),
            models.Index(fields=["target_type", "target_id"], name="idx_audit_target"),
        ]

    def __str__(self) -> str:
        return f"{self.action} on {self.target_type}:{self.target_id} by {self.admin_user_id}"


class ModerationActionType(models.TextChoices):
    """Types of moderation actions an admin can perform on a user."""

    WARNED = "warned", "Warned"
    SUSPENDED = "suspended", "Suspended"
    DELETED = "deleted", "Deleted"
    REACTIVATED = "reactivated", "Reactivated"


class ModerationAction(TimestampedModel):
    """Record of a moderation action taken against a user.

    Attributes:
        user: The user who was moderated.
        action_type: Type of moderation action.
        reason: Human-readable reason for the action.
        admin_user: The admin who performed the action.
        report: Optional link to the report that triggered this action.
        metadata: Additional context stored as JSON.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="moderation_actions_received",
    )
    action_type = models.CharField(
        max_length=50,
        choices=ModerationActionType.choices,
        db_index=True,
    )
    reason = models.TextField()
    admin_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="moderation_actions_performed",
    )
    report = models.ForeignKey(
        "moderation.Report",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="moderation_actions",
    )
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "moderation_actions"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "-created_at"], name="idx_modaction_user"),
            models.Index(fields=["action_type"], name="idx_modaction_type"),
        ]

    def __str__(self) -> str:
        return f"{self.action_type} on user {self.user_id} by {self.admin_user_id}"


# ──────────────────────────────────────────────
#  Analytics
# ──────────────────────────────────────────────


class DailyAnalytics(models.Model):
    """Pre-aggregated daily platform metrics for fast dashboard reads.

    Populated by a Celery Beat task at 00:05 UTC daily.
    """

    id = models.AutoField(primary_key=True)
    date = models.DateField(unique=True, db_index=True)
    total_users = models.IntegerField(default=0)
    new_users = models.IntegerField(default=0)
    dau = models.IntegerField(default=0, help_text="Daily active users")
    wau = models.IntegerField(default=0, help_text="Weekly active users")
    mau = models.IntegerField(default=0, help_text="Monthly active users")
    posts_count = models.IntegerField(default=0)
    comments_count = models.IntegerField(default=0)
    reports_received = models.IntegerField(default=0)
    reports_resolved = models.IntegerField(default=0)
    avg_resolution_minutes = models.FloatField(default=0.0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "daily_analytics"
        ordering = ["-date"]
        indexes = [
            models.Index(fields=["-date"], name="idx_daily_analytics_date"),
        ]

    def __str__(self) -> str:
        return f"Analytics for {self.date}"


# ──────────────────────────────────────────────
#  Contact / Support
# ──────────────────────────────────────────────


class ContactStatus(models.TextChoices):
    """Status lifecycle for contact/support tickets."""

    PENDING = "pending", "Pending"
    IN_PROGRESS = "in_progress", "In Progress"
    RESOLVED = "resolved", "Resolved"


class ContactSenderType(models.TextChoices):
    """Participant types for normalized support conversation messages."""

    USER = "USER", "User"
    ADMIN = "ADMIN", "Admin"
    SYSTEM = "SYSTEM", "System"


class ContactMessage(models.Model):
    """A support message submitted by a user or visitor.

    Attributes:
        name: Sender's name.
        email: Sender's email for replies.
        message: The support message body.
        source: Origin of the ticket (mobile_app, landing_page, admin_dashboard).
        brand: Optional brand/app context for routed submissions.
        status: Current ticket status.
        replied_at: Timestamp of the first admin reply.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    email = models.EmailField()
    message = models.TextField()
    requester_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="contact_messages",
    )
    topic = models.CharField(max_length=100, blank=True, default="")
    source = models.CharField(max_length=50, default="mobile_app", db_index=True)
    brand = models.CharField(max_length=50, blank=True, default="")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=ContactStatus.choices,
        default=ContactStatus.PENDING,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_message_at = models.DateTimeField(null=True, blank=True, db_index=True)
    replied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "contact_messages"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "-created_at"], name="idx_contact_status_created"),
            models.Index(fields=["source", "-created_at"], name="idx_contact_source_created"),
            models.Index(
                fields=["requester_user", "-created_at"],
                name="idx_contact_requester_created",
            ),
            models.Index(
                fields=["requester_user", "-last_message_at"],
                name="idx_contact_requester_last",
            ),
        ]

    def __str__(self) -> str:
        return f"Contact from {self.name} ({self.email})"


class ContactConversationMessage(models.Model):
    """A normalized user, admin, or system message in a support thread."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    contact = models.ForeignKey(
        ContactMessage,
        on_delete=models.CASCADE,
        related_name="conversation_messages",
    )
    sender_type = models.CharField(max_length=10, choices=ContactSenderType.choices)
    sender_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_conversation_messages",
    )
    message = models.TextField()
    client_message_id = models.CharField(max_length=100, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "contact_conversation_messages"
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["contact", "client_message_id"],
                condition=models.Q(client_message_id__isnull=False),
                name="uq_contact_client_message",
            ),
        ]
        indexes = [
            models.Index(
                fields=["contact", "created_at", "id"],
                name="idx_contact_message_cursor",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.sender_type} message in {self.contact_id}"

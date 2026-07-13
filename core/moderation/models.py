"""
Moderation models for the Ziona platform.

Defines the Report model for user-submitted content reports.
"""

from django.db import models

from core.shared.models import TimestampedModel


class ReportReason(models.TextChoices):
    """Reasons a user can report content."""

    DISRESPECTFUL_TO_FAITH = "disrespectful_to_faith", "Disrespectful to Christ/church"
    MISUSE_SCRIPTURE = "misuse_scripture", "Misuse of scripture/unbiblical doctrine"
    ATTACKING_CHURCH = "attacking_church", "Attacking a church, leader, or group"
    SCAM = "scam", "Scam or fraud"
    HATE_SPEECH = "hate_speech", "Hate speech or discrimination"
    POLICY_VIOLATION = "policy_violation", "Restricted or against policy content"
    OTHER = "other", "Other"


class ReportStatus(models.TextChoices):
    """Status of a content report."""

    PENDING = "pending", "Pending"
    REVIEWED = "reviewed", "Reviewed"
    ACTIONED = "actioned", "Actioned"
    DISMISSED = "dismissed", "Dismissed"


class ModerationActionChoice(models.TextChoices):
    """Admin actions that can be taken on a report."""

    DISMISS = "dismiss", "Dismiss"
    HIDE_CONTENT = "hide_content", "Hide Content"
    WARN_USER = "warn_user", "Warn User"
    DELETE_CONTENT = "delete_content", "Delete Content"
    DELETE_AND_WARN = "delete_and_warn", "Delete and Warn"
    RESTORE_CONTENT = "restore_content", "Restore Content"


class Report(TimestampedModel):
    """A user-submitted report against a post, comment, or profile.

    Uses a generic target_type + target_id approach for flexibility,
    while keeping FK relations to Post/Comment for query convenience.

    Attributes:
        reporter: The user who filed the report.
        target_type: Type of reported content (post, comment, profile).
        target_id: UUID of the reported entity.
        post: FK to reported post (for query joins, nullable).
        comment: FK to reported comment (for query joins, nullable).
        reason: Categorized reason for the report.
        description: Optional free-text description (required for "other").
        status: Current review status.
        reviewed_by: Admin who reviewed the report.
        reviewed_at: Timestamp of review.
        action: Admin moderation action taken.
        internal_notes: Admin-only notes (never visible to users).
    """

    TARGET_TYPE_CHOICES = (
        ("post", "Post"),
        ("comment", "Comment"),
        ("profile", "Profile"),
    )

    reporter = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="reports_made",
    )
    target_type = models.CharField(
        max_length=20,
        choices=TARGET_TYPE_CHOICES,
        default="post",
        db_index=True,
    )
    target_id = models.UUIDField(null=True, blank=True, db_index=True)
    post = models.ForeignKey(
        "posts.Post",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,  # Preserve audit trail when post is deleted
        related_name="reports",
    )
    comment = models.ForeignKey(
        "engagement.Comment",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,  # Preserve audit trail when comment is deleted
        related_name="reports",
    )
    reason = models.CharField(
        max_length=50,
        choices=ReportReason.choices,
    )
    description = models.TextField(max_length=500, blank=True, null=True)
    status = models.CharField(
        max_length=20,
        choices=ReportStatus.choices,
        default=ReportStatus.PENDING,
        db_index=True,
    )
    reviewed_by = models.ForeignKey(
        "users.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reports_reviewed",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    action = models.CharField(
        max_length=50,
        choices=ModerationActionChoice.choices,
        null=True,
        blank=True,
    )
    internal_notes = models.TextField(
        blank=True,
        default="",
        help_text="Admin-only notes. Never visible to end users.",
    )

    class Meta:
        db_table = "reports"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status"], name="idx_report_status"),
            models.Index(fields=["reporter"], name="idx_report_reporter"),
            models.Index(fields=["post"], name="idx_report_post"),
            models.Index(fields=["comment"], name="idx_report_comment"),
            models.Index(fields=["target_type", "target_id"], name="idx_report_target"),
        ]
        constraints = [
            # Prevent the same user from filing duplicate reports for the
            # same content+reason (idempotency at DB level — Issue #7).
            models.UniqueConstraint(
                fields=["reporter", "target_type", "target_id", "reason"],
                name="unique_user_report",
            )
        ]

    def __str__(self) -> str:
        """Return string representation."""
        return f"Report({self.reason}) {self.target_type}:{self.target_id} by {self.reporter_id}"

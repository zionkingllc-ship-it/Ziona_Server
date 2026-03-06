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


class Report(TimestampedModel):
    """A user-submitted report against a post or comment.

    At least one of post or comment must be set (enforced by CHECK constraint).

    Attributes:
        reporter: The user who filed the report.
        post: The reported post (if reporting a post).
        comment: The reported comment (if reporting a comment).
        reason: Categorized reason for the report.
        description: Optional free-text description (required for "other").
        status: Current review status.
        reviewed_by: Admin who reviewed the report.
        reviewed_at: Timestamp of review.
    """

    reporter = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="reports_made",
    )
    post = models.ForeignKey(
        "posts.Post",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="reports",
    )
    comment = models.ForeignKey(
        "engagement.Comment",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
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

    class Meta:
        db_table = "reports"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                check=(models.Q(post__isnull=False) | models.Q(comment__isnull=False)),
                name="ck_report_has_target",
            ),
        ]
        indexes = [
            models.Index(fields=["status"], name="idx_report_status"),
            models.Index(fields=["reporter"], name="idx_report_reporter"),
            models.Index(fields=["post"], name="idx_report_post"),
            models.Index(fields=["comment"], name="idx_report_comment"),
        ]

    def __str__(self) -> str:
        """Return string representation."""
        target = f"post={self.post_id}" if self.post_id else f"comment={self.comment_id}"
        return f"Report({self.reason}) {target} by {self.reporter_id}"

import uuid

from django.conf import settings
from django.core.validators import MinLengthValidator
from django.db import models
from django.db.models import Q

User = settings.AUTH_USER_MODEL


class Circle(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, validators=[MinLengthValidator(3)])
    description = models.TextField()
    cover_image = models.URLField(max_length=500)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name="created_circles"
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "circles"
        indexes = [
            models.Index(
                fields=["is_active"],
                condition=Q(deleted_at__isnull=True),
                name="idx_circles_active",
            ),
            models.Index(fields=["-created_at"], name="idx_circles_created"),
        ]

    def __str__(self):
        return self.name

    def get_member_count(self) -> int:
        return self.memberships.count()

    def get_member_previews(self, limit=4):
        """
        Get first N members for Circle card preview
        Order by:
        1. Admins first
        2. Then by joined_at (earliest members)
        """
        return [
            membership.user
            for membership in self.memberships.select_related("user").order_by("role", "joined_at")[
                :limit
            ]
        ]

    def is_user_subscribed(self, user_id) -> bool:
        if not user_id:
            return False
        return self.memberships.filter(user_id=user_id).exists()

    def get_active_anchor(self):
        from core.circles.anchor_services import get_active_anchor

        return get_active_anchor(self.id)


class CircleMembership(models.Model):
    ROLE_CHOICES = (
        ("member", "Member"),
        ("moderator", "Moderator"),
        ("admin", "Admin"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    circle = models.ForeignKey(Circle, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="circle_memberships")
    role = models.CharField(max_length=50, choices=ROLE_CHOICES, default="member")
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "circle_memberships"
        constraints = [
            models.UniqueConstraint(fields=["circle", "user"], name="unique_circle_user_membership")
        ]
        indexes = [
            models.Index(fields=["circle"], name="idx_memberships_circle"),
            models.Index(fields=["user"], name="idx_memberships_user"),
            models.Index(fields=["circle", "user"], name="idx_memberships_composite"),
        ]

    def __str__(self):
        return f"{self.user.email} - {self.circle.name} ({self.role})"

    def is_admin(self) -> bool:
        return self.role == "admin"

    def is_moderator(self) -> bool:
        return self.role in ["admin", "moderator"]


class CircleRule(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    rule_number = models.IntegerField()
    title = models.CharField(max_length=255)
    description = models.TextField()
    is_default = models.BooleanField(default=True)

    class Meta:
        db_table = "circle_rules"
        ordering = ["rule_number"]

    def __str__(self):
        return f"Rule {self.rule_number}: {self.title}"

    @classmethod
    def get_default_rules(cls):
        return cls.objects.filter(is_default=True)


class Anchor(models.Model):
    """Daily curated content that Circle members respond to. Expires after 24 hours."""

    ANCHOR_TYPE_CHOICES = (
        ("bible_verse", "Bible Verse"),
        ("devotional", "Devotional"),
        ("image", "Image"),
        ("video", "Video"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    circle = models.ForeignKey(Circle, on_delete=models.CASCADE, related_name="anchors")
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name="created_anchors"
    )
    anchor_type = models.CharField(max_length=20, choices=ANCHOR_TYPE_CHOICES)
    title = models.CharField(max_length=255)
    content = models.TextField(blank=True)

    # Scripture reference fields (for bible_verse type)
    scripture_book = models.CharField(max_length=100, blank=True)
    scripture_chapter = models.IntegerField(null=True, blank=True)
    scripture_verse_start = models.IntegerField(null=True, blank=True)
    scripture_verse_end = models.IntegerField(null=True, blank=True)
    scripture_translation = models.CharField(max_length=20, blank=True, default="KJV")
    scripture_text = models.TextField(blank=True)

    # Media fields (for image/video type)
    media_url = models.URLField(max_length=500, blank=True)

    # Lifecycle
    published_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    is_notified = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "anchors"
        ordering = ["-published_at"]
        indexes = [
            models.Index(fields=["circle", "-published_at"], name="idx_anchors_circle_published"),
            models.Index(
                fields=["circle", "expires_at"],
                condition=Q(deleted_at__isnull=True),
                name="idx_anchors_active",
            ),
        ]

    def __str__(self):
        return f"{self.title} ({self.circle.name})"

    @property
    def is_active(self):
        from django.utils import timezone

        now = timezone.now()
        return self.published_at <= now < self.expires_at and self.deleted_at is None

    @property
    def is_expired(self):
        from django.utils import timezone

        return timezone.now() >= self.expires_at

    def get_time_remaining(self) -> str:
        """Returns time until expiration formatted as '23h 10m 23s'"""
        from core.circles.anchor_services import calculate_time_remaining

        return calculate_time_remaining(self.expires_at)

    def get_response_count(self) -> int:
        return self.responses.filter(parent_response__isnull=True, deleted_at__isnull=True).count()


class AnchorPage(models.Model):
    """Multi-page content for devotional anchors."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    anchor = models.ForeignKey(Anchor, on_delete=models.CASCADE, related_name="pages")
    page_number = models.IntegerField()
    title = models.CharField(max_length=255, blank=True)
    content = models.TextField()
    media_url = models.URLField(max_length=500, blank=True)

    class Meta:
        db_table = "anchor_pages"
        ordering = ["page_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["anchor", "page_number"], name="unique_anchor_page_number"
            )
        ]

    def __str__(self):
        return f"Page {self.page_number} of {self.anchor.title}"


# ──────────────────────────────────────────────
#  PHASE 3: Response System Models
# ──────────────────────────────────────────────


class AnchorResponse(models.Model):
    """A user's reflection/response to an Anchor, or a reply to another response."""

    RESPONSE_TYPE_CHOICES = (
        ("reflection", "Reflection"),
        ("prayer", "Prayer"),
        ("question", "Question"),
        ("reply", "Reply"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    anchor = models.ForeignKey(Anchor, on_delete=models.CASCADE, related_name="responses")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="anchor_responses")

    # Threading: if null, it's a top-level response. if set, it's a reply.
    parent_response = models.ForeignKey(
        "self", on_delete=models.CASCADE, null=True, blank=True, related_name="replies"
    )

    response_type = models.CharField(
        max_length=20, choices=RESPONSE_TYPE_CHOICES, default="reflection"
    )
    content = models.TextField()
    media_url = models.URLField(max_length=500, blank=True)
    media_type = models.CharField(
        max_length=20, blank=True, choices=(("image", "Image"), ("video", "Video"))
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    # Denormalized count for Trending Sort algorithm: (reaction_count * 2) - hours_since
    reaction_count = models.IntegerField(default=0)

    class Meta:
        db_table = "anchor_responses"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["anchor", "-created_at"], name="idx_responses_anchor_created"),
            models.Index(fields=["user"], name="idx_responses_user"),
            models.Index(fields=["parent_response", "-created_at"], name="idx_responses_parent"),
        ]

    def __str__(self):
        return f"Response by {self.user.email} on {self.anchor.title}"


class AnchorResponseReaction(models.Model):
    """Faith-based reactions to an AnchorResponse."""

    REACTION_TYPE_CHOICES = (
        ("amen", "Amen"),
        ("encouraged", "Encouraged"),
        ("thoughtful", "Thoughtful"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    response = models.ForeignKey(AnchorResponse, on_delete=models.CASCADE, related_name="reactions")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="anchor_reactions")
    reaction_type = models.CharField(max_length=20, choices=REACTION_TYPE_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "anchor_response_reactions"
        constraints = [
            models.UniqueConstraint(
                fields=["response", "user"], name="unique_response_user_reaction"
            )
        ]
        indexes = [
            models.Index(fields=["response"], name="idx_reactions_response"),
            models.Index(fields=["user"], name="idx_reactions_user"),
        ]

    def __str__(self):
        return f"{self.user.email} reacted {self.reaction_type} to response {self.response_id}"


# ──────────────────────────────────────────────
#  PHASE 4: Moderation System Models
# ──────────────────────────────────────────────


class CircleReport(models.Model):
    """Reports for Circle content (Anchors or Responses). Auto-hides after 3 reports."""

    TARGET_TYPE_CHOICES = (
        ("anchor", "Anchor"),
        ("response", "Anchor Response"),
        ("circle", "Circle"),
    )

    STATUS_CHOICES = (
        ("pending", "Pending"),
        ("resolved_kept", "Resolved - Kept"),
        ("resolved_removed", "Resolved - Removed"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reporter = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name="submitted_reports"
    )
    circle = models.ForeignKey(Circle, on_delete=models.CASCADE, related_name="reports")

    target_type = models.CharField(max_length=20, choices=TARGET_TYPE_CHOICES)
    target_id = models.UUIDField()  # Generic ID for the target content

    reason = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")

    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, related_name="resolved_reports"
    )

    class Meta:
        db_table = "circle_reports"
        constraints = [
            models.UniqueConstraint(
                fields=["reporter", "target_type", "target_id"], name="unique_reporter_target"
            )
        ]
        indexes = [
            models.Index(fields=["circle", "status"], name="idx_reports_circle_status"),
            models.Index(fields=["target_type", "target_id"], name="idx_reports_target"),
        ]

    def __str__(self):
        return f"Report {self.id} on {self.target_type} {self.target_id}"

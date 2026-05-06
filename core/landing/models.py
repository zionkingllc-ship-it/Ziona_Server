"""
Landing page models for the Ziona platform.

Covers:
  ContactSubmission  — dual-brand contact form submissions
  WaitlistEntry      — dual-brand waitlist with per-(brand, email) uniqueness
  LegalDocument      — versioned Markdown legal docs (one active per type)
  LegalAcceptance    — user ToS acceptance audit trail
  AppStoreLink       — DB-managed iOS / Android store URLs
  CompanyStat        — key/value stats updated hourly by Celery
"""

import uuid

from django.conf import settings
from django.db import models

# ──────────────────────────────────────────────────────────────
# Shared choices
# ──────────────────────────────────────────────────────────────


class BrandChoice(models.TextChoices):
    ZIONA = "ZIONA", "Ziona"
    ZIONKING = "ZIONKING", "Zion King"


# ──────────────────────────────────────────────────────────────
# ContactSubmission
# ──────────────────────────────────────────────────────────────


class ContactSubmissionStatus(models.TextChoices):
    NEW = "new", "New"
    READ = "read", "Read"
    REPLIED = "replied", "Replied"
    CLOSED = "closed", "Closed"


class ContactSubmission(models.Model):
    """A public contact form submission routed per brand.

    Honeypot field must be empty on submission — if populated, the record
    is saved as spam=True but no emails are sent.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    brand = models.CharField(max_length=10, choices=BrandChoice.choices, db_index=True)
    name = models.CharField(max_length=100)
    email = models.EmailField()
    message = models.TextField()
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    status = models.CharField(
        max_length=10,
        choices=ContactSubmissionStatus.choices,
        default=ContactSubmissionStatus.NEW,
        db_index=True,
    )
    honeypot = models.CharField(max_length=200, blank=True, default="")
    is_spam = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "landing_contact_submissions"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["brand", "status"], name="idx_contact_brand_status"),
        ]

    def __str__(self) -> str:
        return f"{self.brand} contact from {self.name} ({self.email})"


# ──────────────────────────────────────────────────────────────
# WaitlistEntry
# ──────────────────────────────────────────────────────────────


class WaitlistEntry(models.Model):
    """Dual-brand waitlist. Same email can join both brands (ZIONA + ZIONKING)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    brand = models.CharField(max_length=10, choices=BrandChoice.choices, db_index=True)
    email = models.EmailField(db_index=True)
    source = models.CharField(max_length=50, default="landing_page")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    confirmed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "landing_waitlist_entries"
        constraints = [
            models.UniqueConstraint(
                fields=["brand", "email"],
                name="uq_waitlist_brand_email",
            )
        ]
        indexes = [
            models.Index(fields=["brand", "created_at"], name="idx_waitlist_brand_created"),
        ]

    def __str__(self) -> str:
        return f"{self.brand} waitlist: {self.email}"


# ──────────────────────────────────────────────────────────────
# LegalDocument
# ──────────────────────────────────────────────────────────────


class LegalDocumentType(models.TextChoices):
    PRIVACY_POLICY = "privacy_policy", "Privacy Policy"
    TERMS_OF_SERVICE = "terms_of_service", "Terms of Service"
    COMMUNITY_GUIDELINES = "community_guidelines", "Community Guidelines"


class LegalDocument(models.Model):
    """Versioned Markdown legal document.

    Only ONE document per type may be active at a time. The partial unique
    index below enforces this at the database level.
    Admin publishes a new version via updateLegalDocument mutation — the
    service deactivates the old one and creates a new active document.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    type = models.CharField(
        max_length=30,
        choices=LegalDocumentType.choices,
        db_index=True,
    )
    content = models.TextField(help_text="Markdown content.")
    version = models.CharField(max_length=20)
    published_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "landing_legal_documents"
        ordering = ["-created_at"]
        constraints = [
            # Only one active document per type (partial unique index).
            # Inactive historical versions are kept for audit purposes.
            models.UniqueConstraint(
                fields=["type"],
                condition=models.Q(is_active=True),
                name="uq_active_legal_doc_per_type",
            )
        ]

    def __str__(self) -> str:
        return f"{self.type} v{self.version} ({'active' if self.is_active else 'inactive'})"


# ──────────────────────────────────────────────────────────────
# LegalAcceptance
# ──────────────────────────────────────────────────────────────


class LegalAcceptance(models.Model):
    """Audit trail of a user accepting a legal document version."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="legal_acceptances",
    )
    document = models.ForeignKey(
        LegalDocument,
        on_delete=models.PROTECT,
        related_name="acceptances",
    )
    accepted_at = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        db_table = "landing_legal_acceptances"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "document"],
                name="uq_user_legal_acceptance",
            )
        ]

    def __str__(self) -> str:
        return f"{self.user_id} accepted {self.document_id}"


# ──────────────────────────────────────────────────────────────
# AppStoreLink
# ──────────────────────────────────────────────────────────────


class AppStorePlatform(models.TextChoices):
    IOS = "ios", "iOS"
    ANDROID = "android", "Android"


class AppStoreLink(models.Model):
    """DB-managed app store URLs. Admin can update without a code deploy."""

    platform = models.CharField(
        max_length=10,
        choices=AppStorePlatform.choices,
        unique=True,
    )
    url = models.URLField(max_length=500)
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "landing_app_store_links"

    def __str__(self) -> str:
        return f"{self.platform}: {self.url}"


# ──────────────────────────────────────────────────────────────
# CompanyStat
# ──────────────────────────────────────────────────────────────


class CompanyStat(models.Model):
    """Key/value platform statistics refreshed hourly by Celery."""

    key = models.CharField(max_length=50, unique=True)
    value = models.IntegerField(default=0)
    display_value = models.CharField(
        max_length=50,
        blank=True,
        help_text="Human-readable display string, e.g. '2,123+' or '10k+'.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "landing_company_stats"

    def __str__(self) -> str:
        return f"{self.key}: {self.display_value}"

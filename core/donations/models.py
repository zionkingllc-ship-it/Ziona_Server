"""Donation and supporter models for hosted Stripe support flows."""

import uuid

from django.db import models


class DonationType(models.TextChoices):
    ONE_TIME = "one_time", "One-time"
    MONTHLY = "monthly", "Monthly"


class DonationSource(models.TextChoices):
    HOSTED_CHECKOUT = "hosted_checkout", "Hosted checkout"
    GRAPHQL_LEGACY = "graphql_legacy", "Legacy GraphQL"


class DonationStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    EXPIRED = "expired", "Expired"
    CANCELLED = "cancelled", "Cancelled"
    REFUNDED = "refunded", "Refunded"


class SubscriptionStatus(models.TextChoices):
    INCOMPLETE = "incomplete", "Incomplete"
    ACTIVE = "active", "Active"
    TRIALING = "trialing", "Trialing"
    PAST_DUE = "past_due", "Past due"
    UNPAID = "unpaid", "Unpaid"
    CANCELLED = "cancelled", "Cancelled"


class SupportPaymentKind(models.TextChoices):
    INITIAL = "initial", "Initial"
    RECURRING = "recurring", "Recurring"
    REFUND = "refund", "Refund"


class SupportPaymentStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    REFUNDED = "refunded", "Refunded"


class StripeWebhookStatus(models.TextChoices):
    RECEIVED = "received", "Received"
    PROCESSED = "processed", "Processed"
    FAILED = "failed", "Failed"


class SupporterIdentity(models.Model):
    """Canonical supporter identity keyed by normalized email."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="supporter_identity",
    )
    normalized_email = models.EmailField(unique=True, db_index=True)
    contact_email = models.EmailField(blank=True, default="")
    display_name = models.CharField(max_length=150, blank=True, default="")
    stripe_customer_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        unique=True,
        db_index=True,
    )
    is_early_supporter = models.BooleanField(default=False, db_index=True)
    early_supporter_number = models.PositiveIntegerField(
        null=True,
        blank=True,
        unique=True,
    )
    first_supported_at = models.DateTimeField(null=True, blank=True)
    last_supported_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "supporter_identities"
        ordering = ["created_at"]

    def __str__(self) -> str:
        return self.normalized_email


class SupporterProgramState(models.Model):
    """Singleton state used to atomically assign early-supporter slots."""

    key = models.CharField(primary_key=True, max_length=32, default="global", editable=False)
    next_early_supporter_number = models.PositiveIntegerField(default=1)
    assigned_early_supporter_count = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "supporter_program_state"

    def __str__(self) -> str:
        return f"Supporter program state ({self.key})"


class Donation(models.Model):
    """A support intent that roots a hosted Stripe checkout or legacy flow."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_donations",
    )
    supporter_identity = models.ForeignKey(
        SupporterIdentity,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="donations",
    )
    donor_name = models.CharField(max_length=150, blank=True, default="")
    donor_email = models.EmailField(blank=True, default="", db_index=True)
    amount = models.PositiveIntegerField(
        help_text="Amount in cents (for example 1000 = 10.00 USD)."
    )
    currency = models.CharField(max_length=8, default="usd")
    type = models.CharField(max_length=16, choices=DonationType.choices, db_index=True)
    source = models.CharField(
        max_length=24,
        choices=DonationSource.choices,
        default=DonationSource.HOSTED_CHECKOUT,
        db_index=True,
    )
    status = models.CharField(
        max_length=16,
        choices=DonationStatus.choices,
        default=DonationStatus.PENDING,
        db_index=True,
    )
    stripe_payment_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        db_index=True,
        help_text="Legacy Stripe identifier kept for backward compatibility.",
    )
    stripe_payment_intent_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        db_index=True,
    )
    stripe_subscription_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        db_index=True,
    )
    stripe_customer_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        db_index=True,
    )
    checkout_session_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        unique=True,
        db_index=True,
    )
    checkout_url = models.URLField(max_length=2000, blank=True, default="")
    idempotency_key = models.CharField(max_length=128, blank=True, default="", db_index=True)
    metadata = models.JSONField(default=dict, blank=True)
    last_error = models.TextField(blank=True, default="")
    is_early_supporter = models.BooleanField(default=False, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    expired_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "donations"
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["supporter_identity", "created_at"], name="idx_donation_identity_created"
            ),
            models.Index(fields=["user", "status"], name="idx_donation_user_status"),
        ]

    def __str__(self) -> str:
        return f"Donation {self.id} ({self.type}, {self.status})"


class Subscription(models.Model):
    """Stripe subscription lifecycle linked to a monthly donation root."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    donation = models.OneToOneField(
        Donation,
        on_delete=models.CASCADE,
        related_name="subscription",
    )
    user = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_subscriptions",
    )
    supporter_identity = models.ForeignKey(
        SupporterIdentity,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="subscriptions",
    )
    stripe_subscription_id = models.CharField(max_length=255, unique=True, db_index=True)
    stripe_customer_id = models.CharField(max_length=255, blank=True, default="", db_index=True)
    stripe_price_id = models.CharField(max_length=255, blank=True, default="")
    amount = models.PositiveIntegerField(default=0)
    currency = models.CharField(max_length=8, default="usd")
    status = models.CharField(
        max_length=20,
        choices=SubscriptionStatus.choices,
        default=SubscriptionStatus.INCOMPLETE,
        db_index=True,
    )
    cancel_at_period_end = models.BooleanField(default=False)
    billing_cycle_anchor = models.DateTimeField(null=True, blank=True)
    current_period_start = models.DateTimeField(null=True, blank=True)
    current_period_end = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "donations_subscriptions"
        indexes = [
            models.Index(fields=["user", "status"], name="idx_subscription_user_status"),
            models.Index(fields=["supporter_identity", "status"], name="idx_sub_identity_status"),
        ]

    def __str__(self) -> str:
        return f"Subscription {self.stripe_subscription_id} ({self.status})"


class SupportPayment(models.Model):
    """Immutable payment ledger for one-time and recurring support charges."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    donation = models.ForeignKey(
        Donation,
        on_delete=models.CASCADE,
        related_name="payments",
        null=True,
        blank=True,
    )
    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.CASCADE,
        related_name="payments",
        null=True,
        blank=True,
    )
    user = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_payments",
    )
    supporter_identity = models.ForeignKey(
        SupporterIdentity,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payments",
    )
    kind = models.CharField(max_length=16, choices=SupportPaymentKind.choices)
    status = models.CharField(
        max_length=16,
        choices=SupportPaymentStatus.choices,
        default=SupportPaymentStatus.PENDING,
        db_index=True,
    )
    amount = models.PositiveIntegerField(default=0)
    currency = models.CharField(max_length=8, default="usd")
    stripe_payment_intent_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        unique=True,
    )
    stripe_invoice_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        unique=True,
    )
    stripe_charge_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        unique=True,
    )
    paid_at = models.DateTimeField(null=True, blank=True)
    failure_code = models.CharField(max_length=128, blank=True, default="")
    failure_message = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "support_payments"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["subscription", "created_at"], name="idx_pay_sub_created"),
            models.Index(fields=["supporter_identity", "status"], name="idx_pay_identity_status"),
        ]

    def __str__(self) -> str:
        return f"Support payment {self.id} ({self.kind}, {self.status})"


class StripeWebhookEvent(models.Model):
    """Stripe webhook idempotency and observability ledger."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    stripe_event_id = models.CharField(max_length=255, unique=True, db_index=True)
    event_type = models.CharField(max_length=255, db_index=True)
    api_version = models.CharField(max_length=64, blank=True, default="")
    livemode = models.BooleanField(default=False)
    status = models.CharField(
        max_length=16,
        choices=StripeWebhookStatus.choices,
        default=StripeWebhookStatus.RECEIVED,
        db_index=True,
    )
    payload = models.JSONField(default=dict, blank=True)
    processing_error = models.TextField(blank=True, default="")
    processed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "stripe_webhook_events"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Stripe event {self.stripe_event_id} ({self.status})"

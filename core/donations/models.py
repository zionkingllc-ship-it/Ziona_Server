"""
Donation models for the Ziona / ZionKing platform.

Donation    — records a single donation intent (one-time or monthly)
Subscription — tracks a Stripe subscription lifecycle linked to a Donation
"""

import uuid

from django.db import models


class DonationType(models.TextChoices):
    ONE_TIME = "one_time", "One-time"
    MONTHLY = "monthly", "Monthly"


class DonationStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    REFUNDED = "refunded", "Refunded"


class SubscriptionStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    CANCELLED = "cancelled", "Cancelled"
    PAST_DUE = "past_due", "Past Due"


class Donation(models.Model):
    """A single donation record — either one-time or the root of a subscription."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    donor_name = models.CharField(max_length=150)
    donor_email = models.EmailField(db_index=True)
    amount = models.IntegerField(help_text="Amount in cents (e.g. 1000 = $10.00).")
    currency = models.CharField(max_length=3, default="usd")
    type = models.CharField(
        max_length=10,
        choices=DonationType.choices,
        db_index=True,
    )
    status = models.CharField(
        max_length=10,
        choices=DonationStatus.choices,
        default=DonationStatus.PENDING,
        db_index=True,
    )
    stripe_payment_id = models.CharField(
        max_length=255,
        blank=True,
        db_index=True,
        help_text="Stripe PaymentIntent ID (one-time) or SetupIntent ID (monthly).",
    )
    stripe_customer_id = models.CharField(
        max_length=255,
        blank=True,
        db_index=True,
    )
    is_early_supporter = models.BooleanField(
        default=False,
        db_index=True,
        help_text="True for the first 1,000 successful donors.",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "donations"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.donor_name} — ${self.amount / 100:.2f} ({self.type}) [{self.status}]"


class Subscription(models.Model):
    """Stripe subscription lifecycle record linked to a monthly Donation."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    donation = models.OneToOneField(
        Donation,
        on_delete=models.CASCADE,
        related_name="subscription",
    )
    stripe_subscription_id = models.CharField(max_length=255, unique=True, db_index=True)
    stripe_price_id = models.CharField(max_length=255, blank=True)
    status = models.CharField(
        max_length=10,
        choices=SubscriptionStatus.choices,
        default=SubscriptionStatus.ACTIVE,
        db_index=True,
    )
    billing_cycle_anchor = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "donations_subscriptions"

    def __str__(self) -> str:
        return f"Subscription {self.stripe_subscription_id} [{self.status}]"

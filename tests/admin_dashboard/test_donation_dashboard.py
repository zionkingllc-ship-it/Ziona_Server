"""Admin support/donation dashboard: overview, listings, and cancel-subscription."""

from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from core.admin_dashboard.donation_services import AdminDonationService
from core.donations.models import (
    Donation,
    DonationStatus,
    DonationType,
    Subscription,
    SubscriptionStatus,
    SupporterIdentity,
    SupportPayment,
    SupportPaymentKind,
    SupportPaymentStatus,
)
from core.shared.exceptions import AdminError


def _identity(email="supporter@example.com", supported=True):
    return SupporterIdentity.objects.create(
        normalized_email=email,
        contact_email=email,
        display_name="Supporter",
        first_supported_at=timezone.now() if supported else None,
    )


def _donation(identity, amount=1000, dtype=DonationType.ONE_TIME):
    return Donation.objects.create(
        supporter_identity=identity,
        donor_email=identity.contact_email,
        donor_name=identity.display_name,
        amount=amount,
        type=dtype,
        status=DonationStatus.SUCCEEDED,
    )


@pytest.mark.django_db
def test_support_overview_aggregates(authenticated_admin):
    identity = _identity()
    donation = _donation(identity)
    SupportPayment.objects.create(
        donation=donation,
        supporter_identity=identity,
        kind=SupportPaymentKind.INITIAL,
        status=SupportPaymentStatus.SUCCEEDED,
        amount=1000,
    )
    SupportPayment.objects.create(
        donation=donation,
        supporter_identity=identity,
        kind=SupportPaymentKind.RECURRING,
        status=SupportPaymentStatus.SUCCEEDED,
        amount=500,
    )
    SupportPayment.objects.create(
        donation=donation,
        supporter_identity=identity,
        kind=SupportPaymentKind.RECURRING,
        status=SupportPaymentStatus.FAILED,
        amount=500,
    )
    Subscription.objects.create(
        donation=donation,
        supporter_identity=identity,
        stripe_subscription_id="sub_overview",
        amount=300,
        status=SubscriptionStatus.ACTIVE,
    )

    overview = AdminDonationService.get_support_overview()

    assert overview["total_raised_cents"] == 1500  # succeeded initial + recurring
    assert overview["total_raised"] == "$15.00"
    assert overview["unique_supporters"] == 1
    assert overview["mrr_cents"] == 300
    assert overview["mrr"] == "$3.00"
    assert overview["active_subscriptions"] == 1
    assert overview["failed_payments"] == 1


@pytest.mark.django_db
def test_list_donations_paginates(authenticated_admin):
    identity = _identity()
    for _ in range(3):
        _donation(identity)

    result = AdminDonationService.list_donations(page=1, page_size=2)

    assert result["total_count"] == 3
    assert len(result["donations"]) == 2
    assert result["total_pages"] == 2
    first = result["donations"][0]
    assert first["amount"] == "$10.00"
    assert first["type"] == "one_time"
    assert first["donor_email"] == "supporter@example.com"


@pytest.mark.django_db
def test_list_payments_can_filter_failed(authenticated_admin):
    identity = _identity()
    donation = _donation(identity)
    SupportPayment.objects.create(
        donation=donation,
        supporter_identity=identity,
        kind=SupportPaymentKind.RECURRING,
        status=SupportPaymentStatus.FAILED,
        amount=500,
        failure_message="card declined",
    )
    SupportPayment.objects.create(
        donation=donation,
        supporter_identity=identity,
        kind=SupportPaymentKind.INITIAL,
        status=SupportPaymentStatus.SUCCEEDED,
        amount=1000,
    )

    result = AdminDonationService.list_payments(status_filter="failed")

    assert result["total_count"] == 1
    assert result["payments"][0]["status"] == "failed"
    assert result["payments"][0]["supporter_email"] == "supporter@example.com"


@pytest.mark.django_db
@patch("core.donations.hosted_services.get_stripe")
def test_cancel_subscription(mock_get_stripe, authenticated_admin):
    mock_get_stripe.return_value = MagicMock()
    identity = _identity()
    donation = _donation(identity, amount=300, dtype=DonationType.MONTHLY)
    subscription = Subscription.objects.create(
        donation=donation,
        supporter_identity=identity,
        stripe_subscription_id="sub_to_cancel",
        amount=300,
        status=SubscriptionStatus.ACTIVE,
    )

    result = AdminDonationService.cancel_subscription(
        str(subscription.id), authenticated_admin["user"]
    )

    assert result["subscription"]["status"] == "cancelled"
    subscription.refresh_from_db()
    assert subscription.status == SubscriptionStatus.CANCELLED
    assert subscription.cancelled_at is not None
    mock_get_stripe.return_value.Subscription.cancel.assert_called_once_with("sub_to_cancel")


@pytest.mark.django_db
@patch("core.donations.hosted_services.get_stripe")
def test_cancel_already_cancelled_raises(mock_get_stripe, authenticated_admin):
    mock_get_stripe.return_value = MagicMock()
    identity = _identity()
    donation = _donation(identity, amount=300, dtype=DonationType.MONTHLY)
    subscription = Subscription.objects.create(
        donation=donation,
        supporter_identity=identity,
        stripe_subscription_id="sub_already",
        amount=300,
        status=SubscriptionStatus.CANCELLED,
    )

    with pytest.raises(AdminError):
        AdminDonationService.cancel_subscription(str(subscription.id), authenticated_admin["user"])
    # Stripe must not be called for an already-cancelled subscription.
    mock_get_stripe.return_value.Subscription.cancel.assert_not_called()

"""Per-type minimum support amounts: $5 one-time, $3 monthly."""

import pytest

from core.donations.hosted_services import (
    MIN_MONTHLY_USD,
    MIN_ONE_TIME_USD,
    HostedSupportService,
    amount_to_cents,
)
from core.shared.exceptions import AdminError


def test_amount_to_cents_one_time_minimum():
    assert amount_to_cents(5, minimum_usd=MIN_ONE_TIME_USD) == 500
    assert amount_to_cents("25.00", minimum_usd=MIN_ONE_TIME_USD) == 2500
    with pytest.raises(AdminError):
        amount_to_cents("4.99", minimum_usd=MIN_ONE_TIME_USD)


def test_amount_to_cents_monthly_minimum():
    assert amount_to_cents(3, minimum_usd=MIN_MONTHLY_USD) == 300
    with pytest.raises(AdminError):
        amount_to_cents("2.99", minimum_usd=MIN_MONTHLY_USD)


@pytest.mark.django_db
def test_create_checkout_rejects_below_one_time_minimum():
    # $4 one-time is rejected before any Stripe/DB work (amount is validated first).
    with pytest.raises(AdminError):
        HostedSupportService.create_checkout(
            amount_usd=4, donation_type="one_time", email="supporter@example.com"
        )


@pytest.mark.django_db
def test_create_checkout_rejects_below_monthly_minimum():
    with pytest.raises(AdminError):
        HostedSupportService.create_checkout(
            amount_usd=2, donation_type="monthly", email="supporter@example.com"
        )

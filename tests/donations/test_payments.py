import json

import pytest
from django.test import Client

from core.donations.models import (
    Donation,
    DonationStatus,
    DonationType,
    Subscription,
    SubscriptionStatus,
)
from core.donations.services import DonationService, _get_stripe
from core.shared.exceptions import AdminError


def test_missing_stripe_secret_key_returns_typed_error(settings):
    settings.STRIPE_SECRET_KEY = ""

    with pytest.raises(AdminError) as excinfo:
        _get_stripe()

    assert "Stripe is not configured" in excinfo.value.message


def test_monthly_donation_missing_price_id_returns_typed_error(settings):
    settings.STRIPE_SECRET_KEY = ""
    settings.STRIPE_MONTHLY_PRICE_ID = ""

    with pytest.raises(AdminError) as excinfo:
        DonationService.create_donation(
            amount=500,
            email="supporter@example.com",
            name="Supporter",
            payment_method_id="pm_test",
            donation_type="monthly",
        )

    assert "Stripe is not configured" in excinfo.value.message


def test_stripe_webhook_missing_secret_is_non_crashing(settings):
    settings.STRIPE_WEBHOOK_SECRET = ""

    response = Client().post(
        "/api/webhooks/stripe/",
        data=json.dumps({"type": "payment_intent.succeeded"}),
        content_type="application/json",
    )

    assert response.status_code == 503
    assert response.json()["error"] == "Stripe webhook is not configured"


def test_cancel_subscription_requires_authentication(api_client):
    response = api_client.post(
        "/graphql/",
        data=json.dumps(
            {"query": "mutation { cancelSubscription { success error { code message } } }"}
        ),
        content_type="application/json",
    )

    assert response.status_code == 200
    payload = response.json()["data"]["cancelSubscription"]
    assert payload["success"] is False
    assert payload["error"]["code"] == "UNAUTHENTICATED"


def test_cancel_subscription_rejects_non_owner(db):
    donation = Donation.objects.create(
        donor_name="Other User",
        donor_email="other@example.com",
        amount=1500,
        type=DonationType.MONTHLY,
        status=DonationStatus.SUCCEEDED,
    )
    subscription = Subscription.objects.create(
        donation=donation,
        stripe_subscription_id="sub_non_owner",
        status=SubscriptionStatus.ACTIVE,
    )

    with pytest.raises(AdminError) as exc_info:
        DonationService.cancel_subscription_for_user(
            user_email="owner@example.com",
            subscription_id=subscription.stripe_subscription_id,
        )

    assert exc_info.value.code == "NOT_FOUND"


def test_cancel_subscription_cancels_authenticated_owners_subscription(db, monkeypatch):
    class _FakeStripeSubscription:
        cancelled = []

        @classmethod
        def cancel(cls, subscription_id):
            cls.cancelled.append(subscription_id)

    class _FakeStripe:
        Subscription = _FakeStripeSubscription

    donation = Donation.objects.create(
        donor_name="Owner",
        donor_email="owner@example.com",
        amount=1500,
        type=DonationType.MONTHLY,
        status=DonationStatus.SUCCEEDED,
    )
    subscription = Subscription.objects.create(
        donation=donation,
        stripe_subscription_id="sub_owner",
        status=SubscriptionStatus.ACTIVE,
    )

    monkeypatch.setattr("core.donations.services._get_stripe", lambda: _FakeStripe)

    result = DonationService.cancel_subscription_for_user(user_email="owner@example.com")

    subscription.refresh_from_db()
    assert result == {"cancelled": True}
    assert _FakeStripeSubscription.cancelled == ["sub_owner"]
    assert subscription.status == SubscriptionStatus.CANCELLED
    assert subscription.cancelled_at is not None

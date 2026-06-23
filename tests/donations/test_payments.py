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


@pytest.mark.django_db
def test_hosted_one_time_checkout_creates_pending_donation(settings, monkeypatch):
    settings.STRIPE_SECRET_KEY = "sk_test_placeholder"  # pragma: allowlist secret
    settings.STRIPE_ONE_TIME_PRODUCT_ID = "prod_once"
    settings.STRIPE_CHECKOUT_SUCCESS_URL = "https://ziona.app/support/success"
    settings.STRIPE_CHECKOUT_CANCEL_URL = "https://ziona.app/support"

    class _Session:
        @staticmethod
        def create(**kwargs):
            assert kwargs["mode"] == "payment"
            assert kwargs["line_items"][0]["price_data"]["unit_amount"] == 1250
            return {"id": "cs_test_once", "url": "https://checkout.stripe.test/once"}

    class _Checkout:
        Session = _Session

    class _FakeStripe:
        checkout = _Checkout

    monkeypatch.setattr(
        "core.donations.hosted_services.get_stripe",
        lambda: _FakeStripe,
    )

    response = Client().post(
        "/api/payments/support-once",
        data=json.dumps(
            {
                "amountUsd": "12.50",
                "email": "supporter@example.com",
                "name": "Supporter",
            }
        ),
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="support-once-request-1",
    )

    assert response.status_code == 201
    body = response.json()["data"]
    assert body["checkoutSessionId"] == "cs_test_once"
    donation = Donation.objects.get(checkout_session_id="cs_test_once")
    assert donation.amount == 1250
    assert donation.status == DonationStatus.PENDING
    assert donation.idempotency_key == "support-once-request-1"


@pytest.mark.django_db
def test_hosted_checkout_idempotency_reuses_existing_session(settings, monkeypatch):
    settings.STRIPE_SECRET_KEY = "sk_test_placeholder"  # pragma: allowlist secret
    settings.STRIPE_ONE_TIME_PRODUCT_ID = "prod_once"
    settings.STRIPE_CHECKOUT_SUCCESS_URL = "https://ziona.app/support/success"
    settings.STRIPE_CHECKOUT_CANCEL_URL = "https://ziona.app/support"
    calls = []

    class _Session:
        @staticmethod
        def create(**kwargs):
            calls.append(kwargs)
            return {"id": "cs_reused", "url": "https://checkout.stripe.test/reused"}

    class _Checkout:
        Session = _Session

    class _FakeStripe:
        checkout = _Checkout

    monkeypatch.setattr(
        "core.donations.hosted_services.get_stripe",
        lambda: _FakeStripe,
    )
    payload = json.dumps(
        {
            "amountUsd": 10,
            "email": "repeat@example.com",
            "name": "Repeat",
        }
    )
    client = Client()
    first = client.post(
        "/api/payments/support-once",
        data=payload,
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="same-support-request",
    )
    second = client.post(
        "/api/payments/support-once",
        data=payload,
        content_type="application/json",
        HTTP_IDEMPOTENCY_KEY="same-support-request",
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert len(calls) == 1
    assert Donation.objects.filter(idempotency_key="same-support-request").count() == 1


@pytest.mark.django_db
def test_stripe_webhook_is_idempotent(settings, monkeypatch):
    from core.donations.models import StripeWebhookEvent, StripeWebhookStatus

    settings.STRIPE_SECRET_KEY = "sk_test_placeholder"  # pragma: allowlist secret
    settings.STRIPE_WEBHOOK_SECRET = "whsec_placeholder"  # pragma: allowlist secret
    event = {
        "id": "evt_duplicate",
        "type": "checkout.session.completed",
        "api_version": "2025-01-01",
        "livemode": False,
        "data": {"object": {"id": "cs_duplicate"}},
    }
    calls = []

    monkeypatch.setattr(
        "stripe.Webhook.construct_event",
        lambda payload, signature, secret: event,
    )
    monkeypatch.setattr(
        "core.donations.webhooks.HostedSupportService.process_webhook_event",
        lambda received: calls.append(received),
    )

    client = Client()
    payload = json.dumps(event)
    first = client.post(
        "/api/webhooks/stripe/",
        data=payload,
        content_type="application/json",
        HTTP_STRIPE_SIGNATURE="test-signature",
    )
    second = client.post(
        "/api/webhooks/stripe/",
        data=payload,
        content_type="application/json",
        HTTP_STRIPE_SIGNATURE="test-signature",
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert len(calls) == 1
    record = StripeWebhookEvent.objects.get(stripe_event_id="evt_duplicate")
    assert record.status == StripeWebhookStatus.PROCESSED


@pytest.mark.django_db
def test_successful_payment_assigns_early_supporter(monkeypatch):
    from core.donations.hosted_services import HostedSupportService
    from core.donations.models import SupporterIdentity, SupportPayment

    donation = Donation.objects.create(
        donor_name="First Supporter",
        donor_email="first@example.com",
        amount=2500,
        type=DonationType.ONE_TIME,
        status=DonationStatus.PENDING,
    )
    monkeypatch.setattr(
        HostedSupportService,
        "_queue_confirmation_email",
        lambda donation: None,
    )

    HostedSupportService._handle_payment_intent_succeeded(
        {
            "id": "pi_first",
            "amount_received": 2500,
            "customer": "cus_first",
            "metadata": {"donation_id": str(donation.id)},
        }
    )

    donation.refresh_from_db()
    identity = SupporterIdentity.objects.get(normalized_email="first@example.com")
    assert donation.status == DonationStatus.SUCCEEDED
    assert donation.is_early_supporter is True
    assert identity.is_early_supporter is True
    assert identity.early_supporter_number == 1
    assert SupportPayment.objects.filter(stripe_payment_intent_id="pi_first").count() == 1

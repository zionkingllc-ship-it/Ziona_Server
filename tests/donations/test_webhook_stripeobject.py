"""Regression: the webhook must handle a StripeObject event (no .get())."""

from unittest.mock import patch

import pytest
from django.test import Client

from core.donations.hosted_services import checkout_success_url


class _FakeStripeEvent:
    """Mimics stripe's StripeObject: attribute access works, .get() does NOT."""

    def __init__(self, data):
        self._data = data

    def __getattr__(self, name):
        try:
            return self._data[name]
        except KeyError as exc:  # matches StripeObject raising AttributeError
            raise AttributeError(name) from exc

    def __getitem__(self, name):
        return self._data[name]


@pytest.mark.django_db
def test_webhook_handles_stripeobject_without_get(settings):
    """A StripeObject has no .get(); the view must not 500 on api_version/livemode."""
    settings.STRIPE_WEBHOOK_SECRET = "whsec_test"
    settings.STRIPE_SECRET_KEY = "sk_test_x"

    event = _FakeStripeEvent(
        {
            "id": "evt_test_1",
            "type": "checkout.session.completed",
            "api_version": "2026-05-27.dahlia",
            "livemode": False,
            "data": {"object": {"id": "cs_test_1", "metadata": {}}},
        }
    )

    with patch("stripe.Webhook.construct_event", return_value=event):
        response = Client().post(
            "/api/webhooks/stripe/",
            data=b'{"id":"evt_test_1","type":"checkout.session.completed"}',
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=1,v1=sig",
        )

    # No donation matches cs_test_1, so the handler logs-and-returns; the point is
    # it reaches 200 instead of 500-ing on `event.get(...)`.
    assert response.status_code == 200

    from core.donations.models import StripeWebhookEvent

    logged = StripeWebhookEvent.objects.get(stripe_event_id="evt_test_1")
    assert logged.api_version == "2026-05-27.dahlia"
    assert logged.status == "processed"


def test_checkout_success_url_keeps_session_placeholder_literal(settings):
    """{CHECKOUT_SESSION_ID} must not be percent-encoded, or Stripe won't substitute it."""
    settings.STRIPE_CHECKOUT_SUCCESS_URL = "https://app.example.com/support/success"
    url = checkout_success_url()
    assert "{CHECKOUT_SESSION_ID}" in url
    assert "%7BCHECKOUT_SESSION_ID%7D" not in url

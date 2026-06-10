import json

import pytest
from django.test import Client

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

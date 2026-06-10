"""
Donation service layer.

Wraps all Stripe API calls. Raises AdminError on failure so schema resolvers
can return structured errors without crashing the GraphQL response.
"""

import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.shared.exceptions import AdminError, ErrorCode

logger = logging.getLogger("core.donations")

# Early supporter threshold
_EARLY_SUPPORTER_LIMIT = 1000


def _get_stripe():
    """Return configured stripe module. Raises AdminError if not configured."""
    try:
        import stripe

        stripe.api_key = settings.STRIPE_SECRET_KEY
        if not stripe.api_key:
            raise AdminError("Stripe is not configured.", ErrorCode.VALIDATION_ERROR)
        return stripe
    except ImportError:
        raise AdminError(
            "Stripe library is not installed. Run: pip install stripe",
            ErrorCode.VALIDATION_ERROR,
        ) from None


def _is_early_supporter() -> bool:
    """Return True if we have fewer than 1,000 succeeded donations."""
    from core.donations.models import Donation, DonationStatus

    return Donation.objects.filter(status=DonationStatus.SUCCEEDED).count() < _EARLY_SUPPORTER_LIMIT


class DonationService:
    """Handles donation creation and subscription management via Stripe."""

    @staticmethod
    def create_donation(
        amount: int,
        email: str,
        name: str,
        payment_method_id: str,
        donation_type: str,
        plan_id: str | None = None,
    ) -> dict:
        """Create a Stripe PaymentIntent (one-time) or Subscription (monthly).

        Steps:
          1. Get or create Stripe customer for the email
          2. Attach payment method to customer
          3. Create PaymentIntent (ONE_TIME) or Subscription (MONTHLY)
          4. Persist Donation record (status=pending)
          5. on_commit: send confirmation email when payment succeeds

        Returns:
            {"transaction_id": str, "client_secret": str | None}
        """
        from core.donations.models import Donation, DonationStatus, DonationType

        stripe = _get_stripe()

        try:
            # Step 1: Get or create Stripe customer
            customers = stripe.Customer.list(email=email, limit=1)
            if customers.data:
                customer = customers.data[0]
            else:
                customer = stripe.Customer.create(email=email, name=name)

            customer_id = customer["id"]

            # Step 2: Attach payment method
            stripe.PaymentMethod.attach(payment_method_id, customer=customer_id)
            stripe.Customer.modify(
                customer_id,
                invoice_settings={"default_payment_method": payment_method_id},
            )

            with transaction.atomic():
                if donation_type.upper() == DonationType.ONE_TIME.upper():
                    # One-time: create PaymentIntent
                    intent = stripe.PaymentIntent.create(
                        amount=amount,
                        currency="usd",
                        customer=customer_id,
                        payment_method=payment_method_id,
                        confirm=True,
                        automatic_payment_methods={"enabled": True, "allow_redirects": "never"},
                        metadata={"donor_name": name, "donor_email": email},
                    )
                    donation = Donation.objects.create(
                        donor_name=name,
                        donor_email=email,
                        amount=amount,
                        type=DonationType.ONE_TIME,
                        status=(
                            DonationStatus.SUCCEEDED
                            if intent["status"] == "succeeded"
                            else DonationStatus.PENDING
                        ),
                        stripe_payment_id=intent["id"],
                        stripe_customer_id=customer_id,
                        is_early_supporter=_is_early_supporter(),
                    )
                    return {
                        "transaction_id": str(donation.id),
                        "client_secret": intent.get("client_secret"),
                    }

                # Monthly: create Subscription
                plan_id = plan_id or getattr(settings, "STRIPE_MONTHLY_PRICE_ID", "")
                if not plan_id:
                    raise AdminError(
                        "Monthly plan ID is required for subscriptions.",
                        ErrorCode.VALIDATION_ERROR,
                    )

                subscription = stripe.Subscription.create(
                    customer=customer_id,
                    items=[{"price": plan_id}],
                    payment_settings={
                        "payment_method_types": ["card"],
                        "save_default_payment_method": "on_subscription",
                    },
                    expand=["latest_invoice.payment_intent"],
                    metadata={"donor_name": name, "donor_email": email},
                )

                donation = Donation.objects.create(
                    donor_name=name,
                    donor_email=email,
                    amount=amount,
                    type=DonationType.MONTHLY,
                    status=DonationStatus.PENDING,
                    stripe_payment_id=subscription["id"],
                    stripe_customer_id=customer_id,
                    is_early_supporter=_is_early_supporter(),
                )

                from core.donations.models import Subscription

                Subscription.objects.create(
                    donation=donation,
                    stripe_subscription_id=subscription["id"],
                    stripe_price_id=plan_id,
                    billing_cycle_anchor=timezone.datetime.fromtimestamp(
                        subscription["billing_cycle_anchor"],
                        tz=timezone.utc,
                    ),
                )

                client_secret = None
                latest_invoice = subscription.get("latest_invoice")
                if latest_invoice and isinstance(latest_invoice, dict):
                    payment_intent = latest_invoice.get("payment_intent")
                    if payment_intent and isinstance(payment_intent, dict):
                        client_secret = payment_intent.get("client_secret")

                return {
                    "transaction_id": str(donation.id),
                    "client_secret": client_secret,
                }

        except AdminError:
            raise
        except Exception as exc:
            logger.error("create_donation_failed", extra={"error": str(exc)}, exc_info=True)
            raise AdminError(
                f"Payment processing failed: {exc}",
                ErrorCode.VALIDATION_ERROR,
            ) from exc

    @staticmethod
    def cancel_subscription(subscription_id: str) -> dict:
        """Cancel a Stripe subscription by its ID.

        Returns:
            {"cancelled": True}
        """
        from core.donations.models import Subscription, SubscriptionStatus

        stripe = _get_stripe()

        try:
            stripe.Subscription.cancel(subscription_id)
        except Exception as exc:
            logger.error(
                "cancel_subscription_failed",
                extra={"subscription_id": subscription_id, "error": str(exc)},
                exc_info=True,
            )
            raise AdminError(
                f"Failed to cancel subscription: {exc}",
                ErrorCode.VALIDATION_ERROR,
            ) from exc

        Subscription.objects.filter(stripe_subscription_id=subscription_id).update(
            status=SubscriptionStatus.CANCELLED,
            cancelled_at=timezone.now(),
        )
        logger.info(
            "subscription_cancelled",
            extra={"subscription_id": subscription_id},
        )
        return {"cancelled": True}

    @staticmethod
    def get_confirmation(transaction_id: str) -> dict:
        """Return donation confirmation details by internal UUID.

        Returns:
            {"donor_name", "amount_display", "type_display", "created_at"}
        """
        from core.donations.models import Donation

        try:
            donation = Donation.objects.get(id=transaction_id)
        except (Donation.DoesNotExist, Exception):
            raise AdminError("Donation not found.", ErrorCode.NOT_FOUND) from None

        return {
            "donor_name": donation.donor_name,
            "amount_display": f"${donation.amount / 100:.2f}",
            "type_display": "Monthly" if donation.type == "monthly" else "One-time",
            "created_at": donation.created_at.isoformat(),
        }

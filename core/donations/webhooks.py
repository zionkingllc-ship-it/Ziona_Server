"""
Stripe webhook handler — REST endpoint (not GraphQL).

Stripe requires access to the raw request body for signature verification.
Registered at: POST /api/webhooks/stripe/

Handles:
  payment_intent.succeeded     → mark Donation succeeded, early-supporter check, send email
  invoice.payment_failed       → mark Donation failed, send failure email
  customer.subscription.deleted → mark Subscription cancelled
"""

import logging

from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

logger = logging.getLogger("core.donations.webhook")


@csrf_exempt
@require_POST
def stripe_webhook(request: HttpRequest) -> JsonResponse:
    """Verify and dispatch incoming Stripe webhook events."""
    payload = request.body
    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")
    webhook_secret = getattr(settings, "STRIPE_WEBHOOK_SECRET", "")

    try:
        import stripe

        stripe.api_key = getattr(settings, "STRIPE_SECRET_KEY", "")
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except ValueError:
        logger.warning("stripe_webhook_invalid_payload")
        return JsonResponse({"error": "Invalid payload"}, status=400)
    except Exception as exc:
        logger.warning("stripe_webhook_signature_failed", extra={"error": str(exc)})
        return JsonResponse({"error": "Invalid signature"}, status=400)

    event_type = event["type"]
    data = event["data"]["object"]

    logger.info("stripe_event_received", extra={"type": event_type, "id": event["id"]})

    if event_type == "payment_intent.succeeded":
        _handle_payment_succeeded(data)
    elif event_type == "invoice.payment_failed":
        _handle_payment_failed(data)
    elif event_type == "customer.subscription.deleted":
        _handle_subscription_deleted(data)
    else:
        logger.debug("stripe_event_unhandled", extra={"type": event_type})

    return JsonResponse({"received": True}, status=200)


# ──────────────────────────────────────────────────────────────
# Event handlers
# ──────────────────────────────────────────────────────────────


def _handle_payment_succeeded(payment_intent: dict) -> None:
    """Handle payment_intent.succeeded.

    1. Update Donation.status = SUCCEEDED
    2. Set is_early_supporter if under the first-1,000 threshold
    3. Queue confirmation email
    """
    from core.donations.models import Donation, DonationStatus
    from core.donations.services import _EARLY_SUPPORTER_LIMIT

    stripe_id = payment_intent.get("id", "")
    try:
        donation = Donation.objects.get(stripe_payment_id=stripe_id)
    except Donation.DoesNotExist:
        logger.warning("stripe_donation_not_found", extra={"stripe_payment_id": stripe_id})
        return

    succeeded_count = Donation.objects.filter(status=DonationStatus.SUCCEEDED).count()
    is_early = succeeded_count < _EARLY_SUPPORTER_LIMIT

    donation.status = DonationStatus.SUCCEEDED
    donation.is_early_supporter = is_early
    donation.save(update_fields=["status", "is_early_supporter", "updated_at"])

    logger.info(
        "donation_succeeded",
        extra={
            "donation_id": str(donation.id),
            "amount": donation.amount,
            "early_supporter": is_early,
        },
    )

    # Queue confirmation email (non-blocking)
    try:
        from core.shared.tasks.email_tasks import send_email_async

        subject = "Thank you for your donation! 🙏"
        plain = (
            f"Hi {donation.donor_name},\n\n"
            f"Thank you for your generous donation of ${donation.amount / 100:.2f}. "
            f"Your support means everything to us.\n\n"
            f"Transaction ID: {donation.id}\n\n"
            f"God bless you,\nThe Ziona Team"
        )
        send_email_async.delay(
            subject=subject,
            message=plain,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[donation.donor_email],
        )
    except Exception:  # noqa: BLE001
        logger.error("donation_confirmation_email_failed", exc_info=True)


def _handle_payment_failed(invoice: dict) -> None:
    """Handle invoice.payment_failed for subscription payments.

    1. Update Donation.status = FAILED
    2. Queue failure notification email
    """
    from core.donations.models import Donation, DonationStatus

    subscription_id = invoice.get("subscription", "")
    customer_email = invoice.get("customer_email", "")

    updated = Donation.objects.filter(stripe_payment_id=subscription_id).update(
        status=DonationStatus.FAILED, updated_at=timezone.now()
    )

    if updated:
        logger.info("donation_payment_failed", extra={"subscription_id": subscription_id})
    else:
        logger.warning(
            "stripe_subscription_not_found_for_failure",
            extra={"subscription_id": subscription_id},
        )

    # Queue failure email
    if customer_email:
        try:
            from core.shared.tasks.email_tasks import send_email_async

            send_email_async.delay(
                subject="Action required: Payment failed",
                message=(
                    "Hi,\n\nWe were unable to process your recent donation payment. "
                    "Please update your payment method to continue your support.\n\n"
                    "The Ziona Team"
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[customer_email],
            )
        except Exception:  # noqa: BLE001
            logger.error("payment_failed_email_error", exc_info=True)


def _handle_subscription_deleted(subscription: dict) -> None:
    """Handle customer.subscription.deleted.

    1. Update Subscription.status = CANCELLED, set cancelled_at
    """
    from core.donations.models import Subscription, SubscriptionStatus

    stripe_sub_id = subscription.get("id", "")

    updated = Subscription.objects.filter(stripe_subscription_id=stripe_sub_id).update(
        status=SubscriptionStatus.CANCELLED,
        cancelled_at=timezone.now(),
        updated_at=timezone.now(),
    )

    if updated:
        logger.info("subscription_deleted", extra={"stripe_subscription_id": stripe_sub_id})
    else:
        logger.warning(
            "stripe_subscription_not_found_for_deletion",
            extra={"stripe_subscription_id": stripe_sub_id},
        )

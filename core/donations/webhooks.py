"""Verified and idempotent Stripe webhook endpoint."""

import json
import logging

from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core.donations.hosted_services import HostedSupportService, obj_get

logger = logging.getLogger("core.donations.webhook")


@csrf_exempt
@require_POST
def stripe_webhook(request: HttpRequest) -> JsonResponse:
    payload = request.body
    signature = request.META.get("HTTP_STRIPE_SIGNATURE", "")
    webhook_secret = getattr(settings, "STRIPE_WEBHOOK_SECRET", "")
    if not webhook_secret:
        logger.error("stripe_webhook_not_configured")
        return JsonResponse({"error": "Stripe webhook is not configured"}, status=503)

    try:
        import stripe

        stripe.api_key = getattr(settings, "STRIPE_SECRET_KEY", "")
        event = stripe.Webhook.construct_event(payload, signature, webhook_secret)
    except ValueError:
        logger.info("stripe_webhook_invalid_payload")
        return JsonResponse({"error": "Invalid payload"}, status=400)
    except Exception as exc:
        logger.info("stripe_webhook_signature_failed", extra={"error": str(exc)})
        return JsonResponse({"error": "Invalid signature"}, status=400)

    from core.donations.models import StripeWebhookEvent, StripeWebhookStatus

    # construct_event returns a StripeObject (not a plain dict): it has no .get(),
    # so event.get(...) raises AttributeError and 500s the webhook. obj_get reads
    # both dict and StripeObject (dict.get / getattr), keeping this robust.
    event_id = str(obj_get(event, "id", "") or "")
    event_type = str(obj_get(event, "type", "") or "")
    try:
        event_payload = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        event_payload = {"id": event_id, "type": event_type}

    webhook_event, _ = StripeWebhookEvent.objects.get_or_create(
        stripe_event_id=event_id,
        defaults={
            "event_type": event_type,
            "api_version": str(obj_get(event, "api_version", "") or ""),
            "livemode": bool(obj_get(event, "livemode", False)),
            "payload": event_payload,
        },
    )
    if webhook_event.status == StripeWebhookStatus.PROCESSED:
        return JsonResponse({"received": True, "duplicate": True}, status=200)

    try:
        HostedSupportService.process_webhook_event(event)
    except Exception as exc:
        webhook_event.status = StripeWebhookStatus.FAILED
        webhook_event.processing_error = str(exc)[:4000]
        webhook_event.save(update_fields=["status", "processing_error", "updated_at"])
        logger.exception(
            "stripe_webhook_processing_failed",
            extra={"event_id": event_id, "event_type": event_type},
        )
        return JsonResponse({"error": "Webhook processing failed"}, status=500)

    webhook_event.status = StripeWebhookStatus.PROCESSED
    webhook_event.processing_error = ""
    webhook_event.processed_at = timezone.now()
    webhook_event.save(
        update_fields=[
            "status",
            "processing_error",
            "processed_at",
            "updated_at",
        ]
    )
    logger.info(
        "stripe_webhook_processed",
        extra={"event_id": event_id, "event_type": event_type},
    )
    return JsonResponse({"received": True}, status=200)

"""Hosted Stripe Checkout, supporter identity, and webhook orchestration."""

from __future__ import annotations

import logging
from datetime import datetime
from datetime import timezone as datetime_timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.shared.exceptions import AdminError, ErrorCode

logger = logging.getLogger("core.donations")
EARLY_SUPPORTER_LIMIT = 1000
# Per-type minimum support amounts (USD). One-time support starts at $5, monthly
# recurring support at $3, matching the product/UX contract.
MIN_ONE_TIME_USD = Decimal("5.00")
MIN_MONTHLY_USD = Decimal("3.00")
# Absolute floor used as the default when no type-specific minimum is supplied.
MIN_SUPPORT_AMOUNT_USD = Decimal("1.00")


def get_stripe():
    try:
        import stripe
    except ImportError as exc:
        raise AdminError("Stripe library is not installed.", ErrorCode.VALIDATION_ERROR) from exc
    stripe.api_key = getattr(settings, "STRIPE_SECRET_KEY", "")
    if not stripe.api_key:
        raise AdminError("Stripe is not configured.", ErrorCode.VALIDATION_ERROR)
    return stripe


def obj_get(value: Any, key: str, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def stripe_id(value: Any) -> str:
    return value if isinstance(value, str) else str(obj_get(value, "id", "") or "")


def stripe_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromtimestamp(int(value), tz=datetime_timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def require_setting(name: str) -> str:
    value = str(getattr(settings, name, "") or "").strip()
    if not value:
        raise AdminError(f"{name} is not configured.", ErrorCode.VALIDATION_ERROR)
    return value


def amount_to_cents(amount_usd: Any, *, minimum_usd: Decimal = MIN_SUPPORT_AMOUNT_USD) -> int:
    try:
        amount = Decimal(str(amount_usd)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        maximum = Decimal(str(getattr(settings, "STRIPE_SUPPORT_MAX_AMOUNT_USD", "10000")))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise AdminError("Enter a valid support amount.", ErrorCode.VALIDATION_ERROR) from exc
    if amount < minimum_usd or amount > maximum:
        raise AdminError(
            f"Support amount must be between USD {minimum_usd} and USD {maximum}.",
            ErrorCode.VALIDATION_ERROR,
        )
    return int(amount * 100)


def checkout_success_url() -> str:
    url = require_setting("STRIPE_CHECKOUT_SUCCESS_URL")
    if "{CHECKOUT_SESSION_ID}" in url or "checkoutSessionId=" in url:
        return url
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    query.append(("checkoutSessionId", "{CHECKOUT_SESSION_ID}"))
    # safe="{}" keeps the braces literal — Stripe only substitutes the token if
    # it sees "{CHECKOUT_SESSION_ID}", not the percent-encoded "%7B...%7D".
    encoded = urlencode(query, safe="{}")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, encoded, parts.fragment))


class HostedSupportService:
    """Create hosted checkouts and reconcile all Stripe state through webhooks."""

    @classmethod
    def create_checkout(
        cls,
        *,
        amount_usd: Any,
        donation_type: str,
        email: str = "",
        name: str = "",
        user=None,
        idempotency_key: str = "",
    ) -> dict:
        from core.donations.models import (
            Donation,
            DonationSource,
            DonationStatus,
            DonationType,
            SupporterIdentity,
        )

        if donation_type not in DonationType.values:
            raise AdminError("Unsupported support type.", ErrorCode.VALIDATION_ERROR)
        is_monthly = donation_type == DonationType.MONTHLY
        product_setting = (
            "STRIPE_MONTHLY_PRODUCT_ID" if is_monthly else "STRIPE_ONE_TIME_PRODUCT_ID"
        )
        minimum_usd = MIN_MONTHLY_USD if is_monthly else MIN_ONE_TIME_USD
        amount = amount_to_cents(amount_usd, minimum_usd=minimum_usd)
        resolved_email = (getattr(user, "email", "") or email).strip().lower()
        resolved_name = (
            name or getattr(user, "display_name", "") or getattr(user, "username", "")
        ).strip()
        if not resolved_email:
            raise AdminError("Email is required for guest support.", ErrorCode.VALIDATION_ERROR)

        identity, _ = SupporterIdentity.objects.get_or_create(
            normalized_email=resolved_email,
            defaults={
                "user": user,
                "contact_email": resolved_email,
                "display_name": resolved_name,
            },
        )
        identity_updates = []
        if user and identity.user_id != user.id:
            identity.user = user
            identity_updates.append("user")
        if resolved_name and identity.display_name != resolved_name:
            identity.display_name = resolved_name
            identity_updates.append("display_name")
        if identity.contact_email != resolved_email:
            identity.contact_email = resolved_email
            identity_updates.append("contact_email")
        if identity_updates:
            identity.save(update_fields=[*identity_updates, "updated_at"])

        key = (idempotency_key or "").strip()[:128]
        if key:
            existing = Donation.objects.filter(idempotency_key=key).exclude(checkout_url="").first()
            if existing:
                return cls.serialize_checkout(existing)

        donation = Donation.objects.create(
            user=user,
            supporter_identity=identity,
            donor_email=resolved_email,
            donor_name=resolved_name,
            amount=amount,
            currency=str(getattr(settings, "STRIPE_CURRENCY", "usd")).lower(),
            type=donation_type,
            source=DonationSource.HOSTED_CHECKOUT,
            status=DonationStatus.PENDING,
            idempotency_key=key,
        )
        metadata = {
            "donation_id": str(donation.id),
            "donation_type": donation_type,
            "user_id": str(user.id) if user else "",
        }
        price_data = {
            "currency": donation.currency,
            "product": require_setting(product_setting),
            "unit_amount": amount,
        }
        if donation_type == DonationType.MONTHLY:
            price_data["recurring"] = {"interval": "month"}
        params = {
            "mode": "subscription" if donation_type == DonationType.MONTHLY else "payment",
            "line_items": [{"price_data": price_data, "quantity": 1}],
            "success_url": checkout_success_url(),
            "cancel_url": require_setting("STRIPE_CHECKOUT_CANCEL_URL"),
            "client_reference_id": str(donation.id),
            "metadata": metadata,
        }
        if identity.stripe_customer_id:
            params["customer"] = identity.stripe_customer_id
        else:
            params["customer_email"] = resolved_email
        if donation_type == DonationType.MONTHLY:
            params["subscription_data"] = {"metadata": metadata}
        else:
            params["payment_intent_data"] = {"metadata": metadata}
        try:
            options = {"idempotency_key": key} if key else {}
            session = get_stripe().checkout.Session.create(**params, **options)
        except Exception as exc:
            donation.status = DonationStatus.FAILED
            donation.last_error = str(exc)[:2000]
            donation.save(update_fields=["status", "last_error", "updated_at"])
            logger.exception(
                "support_checkout_create_failed", extra={"donation_id": str(donation.id)}
            )
            raise AdminError(
                "Unable to start checkout. Please try again.",
                ErrorCode.VALIDATION_ERROR,
            ) from exc

        donation.checkout_session_id = stripe_id(session)
        donation.checkout_url = str(obj_get(session, "url", "") or "")
        donation.metadata = metadata
        donation.save(
            update_fields=["checkout_session_id", "checkout_url", "metadata", "updated_at"]
        )
        return cls.serialize_checkout(donation)

    @staticmethod
    def serialize_checkout(donation) -> dict:
        return {
            "transaction_id": str(donation.id),
            "checkout_session_id": donation.checkout_session_id,
            "checkout_url": donation.checkout_url,
            "status": donation.status,
            "type": donation.type,
        }

    @classmethod
    def get_checkout_status(cls, checkout_session_id: str) -> dict:
        from core.donations.models import Donation

        try:
            donation = Donation.objects.select_related("supporter_identity").get(
                checkout_session_id=checkout_session_id
            )
        except Donation.DoesNotExist:
            raise AdminError("Checkout session not found.", ErrorCode.NOT_FOUND) from None
        identity = donation.supporter_identity
        return {
            **cls.serialize_checkout(donation),
            "is_early_supporter": donation.is_early_supporter,
            "early_supporter_number": identity.early_supporter_number if identity else None,
        }

    @staticmethod
    def create_customer_portal_session(*, user) -> dict:
        from core.donations.models import SupporterIdentity

        identity = SupporterIdentity.objects.filter(
            Q(user=user) | Q(normalized_email=user.email.strip().lower())
        ).first()
        if not identity or not identity.stripe_customer_id:
            login_url = str(getattr(settings, "STRIPE_PORTAL_LOGIN_URL", "") or "")
            if login_url:
                return {"portal_url": login_url}
            raise AdminError("No Stripe customer exists for this account.", ErrorCode.NOT_FOUND)
        try:
            session = get_stripe().billing_portal.Session.create(
                customer=identity.stripe_customer_id,
                return_url=require_setting("STRIPE_PORTAL_RETURN_URL"),
            )
        except Exception as exc:
            logger.exception("customer_portal_create_failed", extra={"user_id": str(user.id)})
            raise AdminError(
                "Unable to open the customer portal.",
                ErrorCode.VALIDATION_ERROR,
            ) from exc
        return {"portal_url": str(obj_get(session, "url", ""))}

    @classmethod
    def process_webhook_event(cls, event: Any) -> None:
        event_type = str(obj_get(event, "type", ""))
        data = obj_get(obj_get(event, "data", {}), "object", {})
        handlers = {
            "checkout.session.completed": cls._handle_checkout_completed,
            "checkout.session.async_payment_succeeded": cls._handle_checkout_completed,
            "checkout.session.expired": cls._handle_checkout_expired,
            "payment_intent.succeeded": cls._handle_payment_intent_succeeded,
            "invoice.paid": cls._handle_invoice_paid,
            "invoice.payment_failed": cls._handle_invoice_failed,
            "customer.subscription.created": cls._handle_subscription,
            "customer.subscription.updated": cls._handle_subscription,
            "customer.subscription.deleted": cls._handle_subscription,
        }
        handler = handlers.get(event_type)
        if handler:
            handler(data)
        else:
            logger.info("stripe_event_ignored", extra={"event_type": event_type})

    @staticmethod
    def _donation_from_metadata(data: Any):
        from core.donations.models import Donation

        metadata = obj_get(data, "metadata", {}) or {}
        donation_id = obj_get(metadata, "donation_id", "")
        if donation_id:
            return Donation.objects.filter(id=donation_id).first()
        return None

    @classmethod
    @transaction.atomic
    def _handle_checkout_completed(cls, session: Any) -> None:
        from core.donations.models import Donation

        donation = cls._donation_from_metadata(session)
        if not donation:
            donation = Donation.objects.filter(checkout_session_id=stripe_id(session)).first()
        if not donation:
            logger.warning("checkout_donation_not_found", extra={"session_id": stripe_id(session)})
            return
        customer_details = obj_get(session, "customer_details", {}) or {}
        donation.checkout_session_id = stripe_id(session) or donation.checkout_session_id
        donation.stripe_customer_id = stripe_id(obj_get(session, "customer"))
        donation.stripe_payment_intent_id = stripe_id(obj_get(session, "payment_intent"))
        donation.stripe_subscription_id = stripe_id(obj_get(session, "subscription"))
        donation.donor_email = (
            (
                obj_get(customer_details, "email", "")
                or obj_get(session, "customer_email", "")
                or donation.donor_email
            )
            .strip()
            .lower()
        )
        donation.donor_name = str(obj_get(customer_details, "name", "") or donation.donor_name)
        donation.save(
            update_fields=[
                "checkout_session_id",
                "stripe_customer_id",
                "stripe_payment_intent_id",
                "stripe_subscription_id",
                "donor_email",
                "donor_name",
                "updated_at",
            ]
        )
        identity = cls._sync_identity(donation)
        if donation.type == "monthly" and donation.stripe_subscription_id:
            cls._upsert_subscription(
                donation,
                identity,
                {"id": donation.stripe_subscription_id, "status": "active"},
            )
        if obj_get(session, "payment_status") == "paid":
            cls._mark_succeeded(donation, identity)

    @classmethod
    def _handle_checkout_expired(cls, session: Any) -> None:
        from core.donations.models import Donation, DonationStatus

        donation = cls._donation_from_metadata(session)
        if not donation:
            donation = Donation.objects.filter(checkout_session_id=stripe_id(session)).first()
        if donation and donation.status == DonationStatus.PENDING:
            donation.status = DonationStatus.EXPIRED
            donation.expired_at = timezone.now()
            donation.save(update_fields=["status", "expired_at", "updated_at"])

    @classmethod
    @transaction.atomic
    def _handle_payment_intent_succeeded(cls, intent: Any) -> None:
        from core.donations.models import Donation, SupportPaymentKind

        donation = cls._donation_from_metadata(intent)
        if not donation:
            donation = Donation.objects.filter(
                Q(stripe_payment_intent_id=stripe_id(intent))
                | Q(stripe_payment_id=stripe_id(intent))
            ).first()
        if not donation:
            logger.warning(
                "payment_intent_donation_not_found",
                extra={"payment_intent_id": stripe_id(intent)},
            )
            return
        donation.stripe_payment_intent_id = stripe_id(intent)
        donation.stripe_customer_id = (
            stripe_id(obj_get(intent, "customer")) or donation.stripe_customer_id
        )
        donation.save(
            update_fields=["stripe_payment_intent_id", "stripe_customer_id", "updated_at"]
        )
        identity = cls._sync_identity(donation)
        cls._mark_succeeded(donation, identity)
        cls._record_payment(
            donation=donation,
            identity=identity,
            kind=SupportPaymentKind.INITIAL,
            status="succeeded",
            amount=int(obj_get(intent, "amount_received", donation.amount) or donation.amount),
            payment_intent_id=stripe_id(intent),
            charge_id=stripe_id(obj_get(intent, "latest_charge")),
        )

    @classmethod
    @transaction.atomic
    def _handle_invoice_paid(cls, invoice: Any) -> None:
        from core.donations.models import Donation, SupportPaymentKind

        subscription_id = stripe_id(obj_get(invoice, "subscription"))
        donation = Donation.objects.filter(stripe_subscription_id=subscription_id).first()
        if not donation:
            return
        identity = cls._sync_identity(donation)
        subscription = cls._upsert_subscription(
            donation,
            identity,
            {"id": subscription_id, "status": "active"},
        )
        cls._mark_succeeded(donation, identity)
        cls._record_payment(
            donation=donation,
            subscription=subscription,
            identity=identity,
            kind=SupportPaymentKind.RECURRING,
            status="succeeded",
            amount=int(obj_get(invoice, "amount_paid", donation.amount) or donation.amount),
            invoice_id=stripe_id(invoice),
            payment_intent_id=stripe_id(obj_get(invoice, "payment_intent")),
            charge_id=stripe_id(obj_get(invoice, "charge")),
        )

    @classmethod
    @transaction.atomic
    def _handle_invoice_failed(cls, invoice: Any) -> None:
        from core.donations.models import Donation, SubscriptionStatus, SupportPaymentKind

        subscription_id = stripe_id(obj_get(invoice, "subscription"))
        donation = Donation.objects.filter(stripe_subscription_id=subscription_id).first()
        if not donation:
            return
        identity = cls._sync_identity(donation)
        subscription = cls._upsert_subscription(
            donation,
            identity,
            {"id": subscription_id, "status": SubscriptionStatus.PAST_DUE},
        )
        cls._record_payment(
            donation=donation,
            subscription=subscription,
            identity=identity,
            kind=SupportPaymentKind.RECURRING,
            status="failed",
            amount=int(obj_get(invoice, "amount_due", donation.amount) or donation.amount),
            invoice_id=stripe_id(invoice),
            payment_intent_id=stripe_id(obj_get(invoice, "payment_intent")),
            failure_message="Stripe could not collect this invoice.",
        )

    @classmethod
    @transaction.atomic
    def _handle_subscription(cls, stripe_subscription: Any) -> None:
        from core.donations.models import Donation

        subscription_id = stripe_id(stripe_subscription)
        donation = cls._donation_from_metadata(stripe_subscription)
        if not donation:
            donation = Donation.objects.filter(stripe_subscription_id=subscription_id).first()
        if not donation:
            return
        donation.stripe_subscription_id = subscription_id
        donation.save(update_fields=["stripe_subscription_id", "updated_at"])
        cls._upsert_subscription(donation, cls._sync_identity(donation), stripe_subscription)

    @staticmethod
    def _sync_identity(donation):
        from core.donations.models import SupporterIdentity

        email = (donation.donor_email or getattr(donation.user, "email", "")).strip().lower()
        if not email:
            return None
        identity, _ = SupporterIdentity.objects.get_or_create(
            normalized_email=email,
            defaults={
                "user": donation.user,
                "contact_email": donation.donor_email or email,
                "display_name": donation.donor_name,
                "stripe_customer_id": donation.stripe_customer_id or None,
            },
        )
        changed = []
        values = {
            "user": donation.user,
            "contact_email": donation.donor_email or email,
            "display_name": donation.donor_name,
            "stripe_customer_id": donation.stripe_customer_id or identity.stripe_customer_id,
        }
        for field, value in values.items():
            if value and getattr(identity, field) != value:
                setattr(identity, field, value)
                changed.append(field)
        if changed:
            identity.save(update_fields=[*changed, "updated_at"])
        if donation.supporter_identity_id != identity.id:
            donation.supporter_identity = identity
            donation.save(update_fields=["supporter_identity", "updated_at"])
        return identity

    @classmethod
    def _mark_succeeded(cls, donation, identity) -> None:
        from core.donations.models import DonationStatus

        first_success = donation.status != DonationStatus.SUCCEEDED
        donation.status = DonationStatus.SUCCEEDED
        donation.completed_at = donation.completed_at or timezone.now()
        if identity:
            cls._assign_early_supporter(identity)
            donation.is_early_supporter = identity.is_early_supporter
        donation.save(update_fields=["status", "completed_at", "is_early_supporter", "updated_at"])
        if first_success:
            transaction.on_commit(lambda: cls._queue_confirmation_email(donation))

    @staticmethod
    def _assign_early_supporter(identity) -> None:
        from core.donations.models import SupporterProgramState

        now = timezone.now()
        identity.first_supported_at = identity.first_supported_at or now
        identity.last_supported_at = now
        if not identity.is_early_supporter:
            state, _ = SupporterProgramState.objects.select_for_update().get_or_create(key="global")
            if state.next_early_supporter_number <= EARLY_SUPPORTER_LIMIT:
                identity.is_early_supporter = True
                identity.early_supporter_number = state.next_early_supporter_number
                state.next_early_supporter_number += 1
                state.assigned_early_supporter_count += 1
                state.save(
                    update_fields=[
                        "next_early_supporter_number",
                        "assigned_early_supporter_count",
                        "updated_at",
                    ]
                )
        identity.save(
            update_fields=[
                "first_supported_at",
                "last_supported_at",
                "is_early_supporter",
                "early_supporter_number",
                "updated_at",
            ]
        )
        if identity.user_id:
            from django.core.cache import cache

            cache.delete(f"user_me_data_{identity.user_id}")

    @staticmethod
    def _upsert_subscription(donation, identity, data: Any):
        from core.donations.models import Subscription, SubscriptionStatus

        subscription_id = stripe_id(data) or donation.stripe_subscription_id
        if not subscription_id:
            return None
        raw_status = str(obj_get(data, "status", SubscriptionStatus.INCOMPLETE))
        # Stripe spells it "canceled" (one L); our enum uses "cancelled". Normalize
        # so a cancellation isn't misfiled as INCOMPLETE (which would silently undo
        # an admin cancel when customer.subscription.deleted arrives).
        if raw_status == "canceled":
            raw_status = SubscriptionStatus.CANCELLED
        status = (
            raw_status if raw_status in SubscriptionStatus.values else SubscriptionStatus.INCOMPLETE
        )
        items = obj_get(obj_get(data, "items", {}), "data", []) or []
        price_id = stripe_id(obj_get(items[0], "price")) if items else ""
        subscription, _ = Subscription.objects.update_or_create(
            stripe_subscription_id=subscription_id,
            defaults={
                "donation": donation,
                "user": donation.user,
                "supporter_identity": identity,
                "stripe_customer_id": (
                    stripe_id(obj_get(data, "customer")) or donation.stripe_customer_id
                ),
                "stripe_price_id": price_id,
                "amount": donation.amount,
                "currency": donation.currency,
                "status": status,
                "cancel_at_period_end": bool(obj_get(data, "cancel_at_period_end", False)),
                "billing_cycle_anchor": stripe_datetime(obj_get(data, "billing_cycle_anchor")),
                "current_period_start": stripe_datetime(obj_get(data, "current_period_start")),
                "current_period_end": stripe_datetime(obj_get(data, "current_period_end")),
                "cancelled_at": stripe_datetime(obj_get(data, "canceled_at")),
            },
        )
        return subscription

    @staticmethod
    def _record_payment(
        *,
        donation,
        identity,
        kind,
        status,
        amount,
        subscription=None,
        payment_intent_id="",
        invoice_id="",
        charge_id="",
        failure_message="",
    ) -> None:
        from core.donations.models import SupportPayment

        lookup = {}
        if invoice_id:
            lookup["stripe_invoice_id"] = invoice_id
        elif payment_intent_id:
            lookup["stripe_payment_intent_id"] = payment_intent_id
        else:
            return
        SupportPayment.objects.update_or_create(
            **lookup,
            defaults={
                "donation": donation,
                "subscription": subscription,
                "user": donation.user,
                "supporter_identity": identity,
                "kind": kind,
                "status": status,
                "amount": amount,
                "currency": donation.currency,
                "stripe_payment_intent_id": payment_intent_id or None,
                "stripe_invoice_id": invoice_id or None,
                "stripe_charge_id": charge_id or None,
                "paid_at": timezone.now() if status == "succeeded" else None,
                "failure_message": failure_message,
            },
        )

    @staticmethod
    def _queue_confirmation_email(donation) -> None:
        try:
            from core.emails.services import EmailService

            EmailService.send_support_donation_email(
                user_name=donation.donor_name or "Supporter",
                email=donation.donor_email,
                support_amount=f"USD {donation.amount / 100:.2f}",
                support_date=timezone.localdate().strftime("%b %d, %Y").replace(" 0", " "),
            )
        except Exception:
            logger.exception(
                "donation_confirmation_email_failed",
                extra={"donation_id": str(donation.id)},
            )

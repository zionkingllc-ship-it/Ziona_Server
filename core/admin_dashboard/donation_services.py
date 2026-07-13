"""Admin donation/support dashboard service.

Read models for the support dashboard (total raised, unique supporters, MRR,
active subscriptions, failed payments, donation history) plus the admin action
to cancel an individual subscription. All mutations are audit-logged.
"""

import logging

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from core.admin_dashboard.permissions import log_admin_action
from core.shared.exceptions import AdminError, ErrorCode

logger = logging.getLogger("core.admin_dashboard")

MAX_PAGE_SIZE = 100


def _format_usd(cents: int | None) -> str:
    """Format an integer cent amount as a human-readable USD string."""
    return f"${(cents or 0) / 100:,.2f}"


def _paginate(qs, page: int, page_size: int) -> tuple[list, dict]:
    page = max(1, page)
    page_size = max(1, min(page_size, MAX_PAGE_SIZE))
    total_count = qs.count()
    offset = (page - 1) * page_size
    items = list(qs[offset : offset + page_size])
    meta = {
        "total_count": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total_count + page_size - 1) // page_size),
    }
    return items, meta


class AdminDonationService:
    """Support-dashboard reads and the cancel-subscription action."""

    @staticmethod
    def get_support_overview() -> dict:
        """All-time support metrics for the dashboard cards."""
        from core.donations.models import (
            Subscription,
            SubscriptionStatus,
            SupporterIdentity,
            SupportPayment,
            SupportPaymentStatus,
        )

        # Total raised = every successfully collected charge (one-time + recurring),
        # read from the payment ledger so subscription renewals are counted too.
        raised = (
            SupportPayment.objects.filter(status=SupportPaymentStatus.SUCCEEDED).aggregate(
                total=Sum("amount")
            )["total"]
            or 0
        )
        # A supporter is "counted" once they have at least one successful charge,
        # which is exactly when first_supported_at is stamped.
        unique_supporters = SupporterIdentity.objects.filter(
            first_supported_at__isnull=False
        ).count()
        active_subscriptions_qs = Subscription.objects.filter(status=SubscriptionStatus.ACTIVE)
        mrr = active_subscriptions_qs.aggregate(total=Sum("amount"))["total"] or 0
        active_subscriptions = active_subscriptions_qs.count()
        failed_payments = SupportPayment.objects.filter(status=SupportPaymentStatus.FAILED).count()

        return {
            "total_raised_cents": raised,
            "total_raised": _format_usd(raised),
            "unique_supporters": unique_supporters,
            "mrr_cents": mrr,
            "mrr": _format_usd(mrr),
            "active_subscriptions": active_subscriptions,
            "failed_payments": failed_payments,
        }

    @staticmethod
    def list_donations(
        page: int = 1,
        page_size: int = 20,
        status_filter: str = "",
        type_filter: str = "",
    ) -> dict:
        """Paginated donation history (exportable by the client)."""
        from core.donations.models import Donation

        qs = Donation.objects.select_related("supporter_identity").order_by("-created_at")
        if status_filter:
            qs = qs.filter(status=status_filter)
        if type_filter:
            qs = qs.filter(type=type_filter)

        items, meta = _paginate(qs, page, page_size)
        return {"donations": [_donation_to_dict(d) for d in items], **meta}

    @staticmethod
    def list_subscriptions(page: int = 1, page_size: int = 20, status_filter: str = "") -> dict:
        """Paginated subscriptions with supporter details."""
        from core.donations.models import Subscription

        qs = Subscription.objects.select_related("supporter_identity").order_by("-created_at")
        if status_filter:
            qs = qs.filter(status=status_filter)

        items, meta = _paginate(qs, page, page_size)
        return {"subscriptions": [_subscription_to_dict(s) for s in items], **meta}

    @staticmethod
    def list_payments(page: int = 1, page_size: int = 20, status_filter: str = "") -> dict:
        """Paginated support payments (pass status='failed' for failed payments)."""
        from core.donations.models import SupportPayment

        qs = SupportPayment.objects.select_related("supporter_identity", "donation").order_by(
            "-created_at"
        )
        if status_filter:
            qs = qs.filter(status=status_filter)

        items, meta = _paginate(qs, page, page_size)
        return {"payments": [_payment_to_dict(p) for p in items], **meta}

    @staticmethod
    @transaction.atomic
    def cancel_subscription(subscription_id: str, admin_user, ip_address: str = "") -> dict:
        """Cancel a supporter's subscription in Stripe and record it locally.

        Cancels immediately. The subsequent customer.subscription.deleted webhook
        reconciles to the same CANCELLED state (status normalization handles
        Stripe's "canceled" spelling), so this is safe against the race.
        """
        from core.donations.hosted_services import get_stripe
        from core.donations.models import Subscription, SubscriptionStatus

        subscription = Subscription.objects.select_for_update().filter(id=subscription_id).first()
        if not subscription:
            raise AdminError("Subscription not found.", ErrorCode.NOT_FOUND)
        if subscription.status == SubscriptionStatus.CANCELLED:
            raise AdminError("Subscription is already cancelled.", ErrorCode.VALIDATION_ERROR)

        try:
            get_stripe().Subscription.cancel(subscription.stripe_subscription_id)
        except AdminError:
            raise
        except Exception as exc:
            logger.exception(
                "admin_subscription_cancel_failed",
                extra={"subscription_id": subscription_id},
            )
            raise AdminError(
                "Unable to cancel the subscription in Stripe. Please try again.",
                ErrorCode.VALIDATION_ERROR,
            ) from exc

        subscription.status = SubscriptionStatus.CANCELLED
        subscription.cancel_at_period_end = False
        subscription.cancelled_at = timezone.now()
        subscription.save(
            update_fields=["status", "cancel_at_period_end", "cancelled_at", "updated_at"]
        )

        log_admin_action(
            admin_user=admin_user,
            action="SUBSCRIPTION_CANCELLED",
            target_type="Subscription",
            target_id=str(subscription.id),
            details={"stripe_subscription_id": subscription.stripe_subscription_id},
            ip_address=ip_address,
        )
        logger.info(
            "admin_subscription_cancelled",
            extra={"subscription_id": subscription_id, "admin_id": str(admin_user.id)},
        )
        return {"subscription": _subscription_to_dict(subscription)}


# ─────────────────────────────────────────
# Private dict mappers
# ─────────────────────────────────────────


def _donation_to_dict(donation) -> dict:
    identity = donation.supporter_identity
    return {
        "id": str(donation.id),
        "donor_name": donation.donor_name or (identity.display_name if identity else ""),
        "donor_email": donation.donor_email or (identity.contact_email if identity else ""),
        "amount_cents": donation.amount,
        "amount": _format_usd(donation.amount),
        "currency": donation.currency,
        "type": donation.type,
        "status": donation.status,
        "is_early_supporter": donation.is_early_supporter,
        "created_at": donation.created_at.isoformat() if donation.created_at else "",
        "completed_at": donation.completed_at.isoformat() if donation.completed_at else None,
    }


def _subscription_to_dict(subscription) -> dict:
    identity = subscription.supporter_identity
    return {
        "id": str(subscription.id),
        "supporter_name": identity.display_name if identity else "",
        "supporter_email": identity.contact_email if identity else "",
        "amount_cents": subscription.amount,
        "amount": _format_usd(subscription.amount),
        "currency": subscription.currency,
        "status": subscription.status,
        "cancel_at_period_end": subscription.cancel_at_period_end,
        "current_period_end": (
            subscription.current_period_end.isoformat() if subscription.current_period_end else None
        ),
        "cancelled_at": (
            subscription.cancelled_at.isoformat() if subscription.cancelled_at else None
        ),
        "stripe_subscription_id": subscription.stripe_subscription_id,
        "created_at": subscription.created_at.isoformat() if subscription.created_at else "",
    }


def _payment_to_dict(payment) -> dict:
    identity = payment.supporter_identity
    donation = payment.donation
    supporter_name = (identity.display_name if identity else "") or (
        donation.donor_name if donation else ""
    )
    supporter_email = (identity.contact_email if identity else "") or (
        donation.donor_email if donation else ""
    )
    return {
        "id": str(payment.id),
        "supporter_name": supporter_name,
        "supporter_email": supporter_email,
        "amount_cents": payment.amount,
        "amount": _format_usd(payment.amount),
        "currency": payment.currency,
        "kind": payment.kind,
        "status": payment.status,
        "failure_message": payment.failure_message,
        "paid_at": payment.paid_at.isoformat() if payment.paid_at else None,
        "created_at": payment.created_at.isoformat() if payment.created_at else "",
    }

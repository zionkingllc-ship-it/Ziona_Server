"""
Donations GraphQL schema.

Strawberry auto-converts snake_case field names to camelCase:
  plan_id          → planId
  payment_method_id → paymentMethodId
  transaction_id   → transactionId
  subscription_id  → subscriptionId
"""

from enum import Enum

import strawberry
from strawberry.types import Info

from core.admin_dashboard.permissions import get_admin_user
from core.shared.exceptions import AdminError
from core.shared.types import ErrorType


@strawberry.enum
class DonationTypeEnum(Enum):
    ONE_TIME = "one_time"
    MONTHLY = "monthly"


@strawberry.type
class DonationPayload:
    """Response for createDonation mutation."""

    success: bool
    transaction_id: str | None = strawberry.field(name="transactionId", default=None)
    client_secret: str | None = strawberry.field(name="clientSecret", default=None)
    error: ErrorType | None = None


@strawberry.type
class CancelPayload:
    """Response for cancelSubscription mutation."""

    success: bool
    error: ErrorType | None = None


@strawberry.type
class DonationConfirmationType:
    """Public donation confirmation details."""

    donor_name: str = strawberry.field(name="donorName")
    amount: str
    type: str
    created_at: str = strawberry.field(name="createdAt")


def _require_admin(info: Info):
    admin = get_admin_user(info)
    if admin is None:
        raise AdminError("Admin access required.", "NOT_AUTHORIZED")
    return admin


# ──────────────────────────────────────────────────────────────
# Queries
# ──────────────────────────────────────────────────────────────


@strawberry.type
class DonationQueries:
    """Public donation queries."""

    @strawberry.field(
        name="donationConfirmation",
        description="Retrieve donation confirmation by transaction ID.",
    )
    def donation_confirmation(
        self,
        transaction_id: str,  # auto → transactionId in GraphQL
    ) -> DonationConfirmationType | None:
        from core.donations.services import DonationService

        try:
            data = DonationService.get_confirmation(transaction_id)
            return DonationConfirmationType(
                donor_name=data["donor_name"],
                amount=data["amount_display"],
                type=data["type_display"],
                created_at=data["created_at"],
            )
        except AdminError:
            return None

    @strawberry.field(name="adminSupportStats")
    def admin_support_stats(self, info: Info) -> strawberry.scalars.JSON:
        _require_admin(info)
        from django.db.models import Count, Sum

        from core.donations.models import (
            Donation,
            DonationStatus,
            Subscription,
            SubscriptionStatus,
            SupporterIdentity,
            SupportPayment,
            SupportPaymentStatus,
        )

        totals = SupportPayment.objects.filter(status=SupportPaymentStatus.SUCCEEDED).aggregate(
            totalAmount=Sum("amount"), paymentCount=Count("id")
        )
        return {
            "totalAmount": totals["totalAmount"] or 0,
            "paymentCount": totals["paymentCount"] or 0,
            "successfulDonations": Donation.objects.filter(status=DonationStatus.SUCCEEDED).count(),
            "activeSubscriptions": Subscription.objects.filter(
                status__in=[SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING]
            ).count(),
            "earlySupporters": SupporterIdentity.objects.filter(is_early_supporter=True).count(),
        }

    @strawberry.field(name="adminSupportDonations")
    def admin_support_donations(
        self,
        info: Info,
        limit: int = 50,
    ) -> strawberry.scalars.JSON:
        _require_admin(info)
        from core.donations.models import Donation

        rows = Donation.objects.select_related("user", "supporter_identity")[
            : max(1, min(limit, 200))
        ]
        return [
            {
                "id": str(row.id),
                "name": row.donor_name,
                "email": row.donor_email,
                "amount": row.amount,
                "currency": row.currency,
                "type": row.type,
                "status": row.status,
                "source": row.source,
                "isEarlySupporter": row.is_early_supporter,
                "createdAt": row.created_at.isoformat(),
            }
            for row in rows
        ]

    @strawberry.field(name="adminSupportSubscriptions")
    def admin_support_subscriptions(
        self,
        info: Info,
        limit: int = 50,
    ) -> strawberry.scalars.JSON:
        _require_admin(info)
        from core.donations.models import Subscription

        rows = Subscription.objects.select_related("donation")[: max(1, min(limit, 200))]
        return [
            {
                "id": str(row.id),
                "stripeSubscriptionId": row.stripe_subscription_id,
                "email": row.donation.donor_email,
                "amount": row.amount,
                "currency": row.currency,
                "status": row.status,
                "cancelAtPeriodEnd": row.cancel_at_period_end,
                "currentPeriodEnd": (
                    row.current_period_end.isoformat() if row.current_period_end else None
                ),
            }
            for row in rows
        ]

    @strawberry.field(name="adminFailedSupportPayments")
    def admin_failed_support_payments(
        self,
        info: Info,
        limit: int = 50,
    ) -> strawberry.scalars.JSON:
        _require_admin(info)
        from core.donations.models import SupportPayment, SupportPaymentStatus

        rows = SupportPayment.objects.filter(status=SupportPaymentStatus.FAILED).select_related(
            "donation"
        )[: max(1, min(limit, 200))]
        return [
            {
                "id": str(row.id),
                "email": row.donation.donor_email if row.donation else "",
                "amount": row.amount,
                "currency": row.currency,
                "failureCode": row.failure_code,
                "failureMessage": row.failure_message,
                "createdAt": row.created_at.isoformat(),
            }
            for row in rows
        ]


# ──────────────────────────────────────────────────────────────
# Mutations
# ──────────────────────────────────────────────────────────────


@strawberry.type
class DonationMutations:
    """Donation mutations."""

    @strawberry.mutation(
        name="createDonation",
        description="Create a one-time or monthly donation via Stripe.",
    )
    def create_donation(
        self,
        info: Info,
        amount: int,
        email: str,
        name: str,
        payment_method_id: str,  # auto → paymentMethodId
        type: DonationTypeEnum = DonationTypeEnum.ONE_TIME,
        plan_id: str | None = None,  # auto → planId; required for MONTHLY
    ) -> DonationPayload:
        from core.donations.services import DonationService

        try:
            result = DonationService.create_donation(
                amount=amount,
                email=email,
                name=name,
                payment_method_id=payment_method_id,
                donation_type=type.value,
                plan_id=plan_id,
            )
            return DonationPayload(
                success=True,
                transaction_id=result["transaction_id"],
                client_secret=result.get("client_secret"),
            )
        except AdminError as e:
            return DonationPayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )

    @strawberry.mutation(
        name="cancelSubscription",
        description="Cancel an active Stripe subscription.",
    )
    def cancel_subscription(
        self,
        info: Info,
        subscription_id: str | None = None,  # auto → subscriptionId
    ) -> CancelPayload:
        from core.donations.services import DonationService
        from core.shared.types import ErrorType

        try:
            from core.authentication.account_status import ensure_account_can_authenticate
            from core.authentication.tokens import (
                TokenError,
                TokenInfrastructureError,
                TokenService,
            )
            from core.users.models import User

            request = info.context.request
            auth_header = request.META.get("HTTP_AUTHORIZATION", "")
            if not auth_header.startswith("Bearer "):
                return CancelPayload(
                    success=False,
                    error=ErrorType(
                        code="UNAUTHENTICATED",
                        message="Authentication required.",
                    ),
                )

            try:
                payload = TokenService.validate_access_token(
                    auth_header[7:],
                    enforce_revocation=True,
                )
            except TokenInfrastructureError:
                return CancelPayload(
                    success=False,
                    error=ErrorType(
                        code="AUTH_SERVICE_UNAVAILABLE",
                        message="Authentication service is temporarily unavailable. Please try again.",
                    ),
                )
            except TokenError:
                return CancelPayload(
                    success=False,
                    error=ErrorType(
                        code="INVALID_TOKEN",
                        message="Invalid or expired token.",
                    ),
                )

            user = User.all_objects.get(id=payload["user_id"])
            ensure_account_can_authenticate(user)

            DonationService.cancel_subscription_for_user(
                user_email=user.email,
                subscription_id=subscription_id,
            )
            return CancelPayload(success=True)
        except AdminError as e:
            return CancelPayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )

    @strawberry.mutation(name="adminCancelSupportSubscription")
    def admin_cancel_support_subscription(
        self,
        info: Info,
        subscription_id: str,
    ) -> CancelPayload:
        try:
            _require_admin(info)
            from django.utils import timezone

            from core.donations.hosted_services import get_stripe
            from core.donations.models import Subscription, SubscriptionStatus

            subscription = Subscription.objects.filter(
                stripe_subscription_id=subscription_id
            ).first()
            if not subscription:
                raise AdminError("Subscription not found.", "NOT_FOUND")
            get_stripe().Subscription.cancel(subscription_id)
            subscription.status = SubscriptionStatus.CANCELLED
            subscription.cancelled_at = timezone.now()
            subscription.save(update_fields=["status", "cancelled_at", "updated_at"])
            return CancelPayload(success=True)
        except AdminError as e:
            return CancelPayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )

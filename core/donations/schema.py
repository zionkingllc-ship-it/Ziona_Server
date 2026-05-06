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
        subscription_id: str,  # auto → subscriptionId
    ) -> CancelPayload:
        from core.donations.services import DonationService

        try:
            DonationService.cancel_subscription(subscription_id)
            return CancelPayload(success=True)
        except AdminError as e:
            return CancelPayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )

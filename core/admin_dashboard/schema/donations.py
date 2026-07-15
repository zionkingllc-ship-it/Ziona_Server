"""Admin support/donations dashboard (overview, listings, cancel).

Split from the former core/admin_dashboard/schema.py (no contract change).
"""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from core.admin_dashboard.permissions import admin_required
from core.shared.types import ErrorType


@strawberry.type
class AdminSupportOverviewType:
    """All-time support metrics for the dashboard cards."""

    mrr: str
    total_raised: str = strawberry.field(name="totalRaised")
    total_raised_cents: int = strawberry.field(name="totalRaisedCents")
    unique_supporters: int = strawberry.field(name="uniqueSupporters")
    mrr_cents: int = strawberry.field(name="mrrCents")
    active_subscriptions: int = strawberry.field(name="activeSubscriptions")
    failed_payments: int = strawberry.field(name="failedPayments")


@strawberry.type
class AdminDonationType:
    """A single donation in the history."""

    id: str
    amount: str
    currency: str
    status: str
    donor_name: str = strawberry.field(name="donorName")
    donor_email: str = strawberry.field(name="donorEmail")
    amount_cents: int = strawberry.field(name="amountCents")
    donation_type: str = strawberry.field(name="donationType")
    is_early_supporter: bool = strawberry.field(name="isEarlySupporter")
    created_at: str = strawberry.field(name="createdAt")
    completed_at: str | None = strawberry.field(name="completedAt", default=None)


@strawberry.type
class AdminDonationsPaginatedType:
    donations: list[AdminDonationType]
    page: int
    total_count: int = strawberry.field(name="totalCount")
    page_size: int = strawberry.field(name="pageSize")
    total_pages: int = strawberry.field(name="totalPages")


@strawberry.type
class AdminSupportSubscriptionType:
    """A supporter subscription with supporter details."""

    id: str
    amount: str
    currency: str
    status: str
    supporter_name: str = strawberry.field(name="supporterName")
    supporter_email: str = strawberry.field(name="supporterEmail")
    amount_cents: int = strawberry.field(name="amountCents")
    cancel_at_period_end: bool = strawberry.field(name="cancelAtPeriodEnd")
    stripe_subscription_id: str = strawberry.field(name="stripeSubscriptionId")
    created_at: str = strawberry.field(name="createdAt")
    current_period_end: str | None = strawberry.field(name="currentPeriodEnd", default=None)
    cancelled_at: str | None = strawberry.field(name="cancelledAt", default=None)


@strawberry.type
class AdminSubscriptionsPaginatedType:
    subscriptions: list[AdminSupportSubscriptionType]
    page: int
    total_count: int = strawberry.field(name="totalCount")
    page_size: int = strawberry.field(name="pageSize")
    total_pages: int = strawberry.field(name="totalPages")


@strawberry.type
class AdminSupportPaymentType:
    """A support payment (initial or recurring) with supporter details."""

    id: str
    amount: str
    currency: str
    kind: str
    status: str
    supporter_name: str = strawberry.field(name="supporterName")
    supporter_email: str = strawberry.field(name="supporterEmail")
    amount_cents: int = strawberry.field(name="amountCents")
    failure_message: str = strawberry.field(name="failureMessage")
    created_at: str = strawberry.field(name="createdAt")
    paid_at: str | None = strawberry.field(name="paidAt", default=None)


@strawberry.type
class AdminSupportPaymentsPaginatedType:
    payments: list[AdminSupportPaymentType]
    page: int
    total_count: int = strawberry.field(name="totalCount")
    page_size: int = strawberry.field(name="pageSize")
    total_pages: int = strawberry.field(name="totalPages")


@strawberry.type
class AdminSubscriptionPayload:
    """Response for the cancel-subscription mutation."""

    success: bool
    subscription: AdminSupportSubscriptionType | None = None
    error: ErrorType | None = None


def _map_donation(data: dict) -> AdminDonationType:
    return AdminDonationType(
        id=data["id"],
        amount=data["amount"],
        currency=data["currency"],
        status=data["status"],
        donor_name=data["donor_name"],
        donor_email=data["donor_email"],
        amount_cents=data["amount_cents"],
        donation_type=data["type"],
        is_early_supporter=data["is_early_supporter"],
        created_at=data["created_at"],
        completed_at=data.get("completed_at"),
    )


def _map_support_subscription(data: dict) -> AdminSupportSubscriptionType:
    return AdminSupportSubscriptionType(
        id=data["id"],
        amount=data["amount"],
        currency=data["currency"],
        status=data["status"],
        supporter_name=data["supporter_name"],
        supporter_email=data["supporter_email"],
        amount_cents=data["amount_cents"],
        cancel_at_period_end=data["cancel_at_period_end"],
        stripe_subscription_id=data["stripe_subscription_id"],
        created_at=data["created_at"],
        current_period_end=data.get("current_period_end"),
        cancelled_at=data.get("cancelled_at"),
    )


def _map_support_payment(data: dict) -> AdminSupportPaymentType:
    return AdminSupportPaymentType(
        id=data["id"],
        amount=data["amount"],
        currency=data["currency"],
        kind=data["kind"],
        status=data["status"],
        supporter_name=data["supporter_name"],
        supporter_email=data["supporter_email"],
        amount_cents=data["amount_cents"],
        failure_message=data["failure_message"],
        created_at=data["created_at"],
        paid_at=data.get("paid_at"),
    )


@strawberry.type
class DonationsAdminQueries:
    @strawberry.field(
        name="adminSupportOverview",
        description="All-time support metrics: raised, supporters, MRR, active subs, failed.",
    )
    @admin_required
    def admin_support_overview(self, info: Info) -> AdminSupportOverviewType:
        from core.admin_dashboard.donation_services import AdminDonationService

        data = AdminDonationService.get_support_overview()
        return AdminSupportOverviewType(
            mrr=data["mrr"],
            total_raised=data["total_raised"],
            total_raised_cents=data["total_raised_cents"],
            unique_supporters=data["unique_supporters"],
            mrr_cents=data["mrr_cents"],
            active_subscriptions=data["active_subscriptions"],
            failed_payments=data["failed_payments"],
        )

    @strawberry.field(name="adminDonations", description="Paginated donation history.")
    @admin_required
    def admin_donations(
        self,
        info: Info,
        page: int = 1,
        page_size: int = 20,
        status: str = "",
        donation_type: str = "",
    ) -> AdminDonationsPaginatedType:
        from core.admin_dashboard.donation_services import AdminDonationService

        result = AdminDonationService.list_donations(
            page=page, page_size=page_size, status_filter=status, type_filter=donation_type
        )
        return AdminDonationsPaginatedType(
            donations=[_map_donation(d) for d in result["donations"]],
            page=result["page"],
            total_count=result["total_count"],
            page_size=result["page_size"],
            total_pages=result["total_pages"],
        )

    @strawberry.field(
        name="adminSupportSubscriptions",
        description="Paginated support subscriptions with supporter details.",
    )
    @admin_required
    def admin_support_subscriptions(
        self,
        info: Info,
        page: int = 1,
        page_size: int = 20,
        status: str = "",
    ) -> AdminSubscriptionsPaginatedType:
        from core.admin_dashboard.donation_services import AdminDonationService

        result = AdminDonationService.list_subscriptions(
            page=page, page_size=page_size, status_filter=status
        )
        return AdminSubscriptionsPaginatedType(
            subscriptions=[_map_support_subscription(s) for s in result["subscriptions"]],
            page=result["page"],
            total_count=result["total_count"],
            page_size=result["page_size"],
            total_pages=result["total_pages"],
        )

    @strawberry.field(
        name="adminSupportPayments",
        description="Paginated support payments. Pass status='failed' for failed payments.",
    )
    @admin_required
    def admin_support_payments(
        self,
        info: Info,
        page: int = 1,
        page_size: int = 20,
        status: str = "",
    ) -> AdminSupportPaymentsPaginatedType:
        from core.admin_dashboard.donation_services import AdminDonationService

        result = AdminDonationService.list_payments(
            page=page, page_size=page_size, status_filter=status
        )
        return AdminSupportPaymentsPaginatedType(
            payments=[_map_support_payment(p) for p in result["payments"]],
            page=result["page"],
            total_count=result["total_count"],
            page_size=result["page_size"],
            total_pages=result["total_pages"],
        )


@strawberry.type
class DonationsAdminMutations:
    @strawberry.mutation(
        name="adminCancelSubscription",
        description="Cancel a supporter's subscription (immediate) and record it.",
    )
    @admin_required
    def admin_cancel_subscription(
        self,
        info: Info,
        subscription_id: str,
    ) -> AdminSubscriptionPayload:
        from core.admin_dashboard.donation_services import AdminDonationService
        from core.shared.exceptions import AdminError

        admin_user = info.context.admin_user
        ip = getattr(info.context, "admin_ip", "")

        try:
            result = AdminDonationService.cancel_subscription(
                subscription_id=subscription_id,
                admin_user=admin_user,
                ip_address=ip,
            )
            return AdminSubscriptionPayload(
                success=True,
                subscription=_map_support_subscription(result["subscription"]),
            )
        except AdminError as e:
            return AdminSubscriptionPayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )

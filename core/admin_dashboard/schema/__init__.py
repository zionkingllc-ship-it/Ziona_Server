"""Admin dashboard GraphQL schema package.

Composes per-domain query/mutation mixins into the two public classes the
root schema imports (AdminDashboardQueries / AdminDashboardMutations) — the
same multiple-inheritance pattern config/graphql_schema.py already uses.
The GraphQL contract is unchanged (guarded by tests/graphql/test_schema_contract.py).
"""

from __future__ import annotations

import strawberry

from core.admin_dashboard.schema.anchors import AnchorsAdminMutations, AnchorsAdminQueries
from core.admin_dashboard.schema.auth import AuthAdminMutations
from core.admin_dashboard.schema.circles import CirclesAdminMutations, CirclesAdminQueries
from core.admin_dashboard.schema.contacts import ContactsAdminMutations, ContactsAdminQueries
from core.admin_dashboard.schema.dashboard import DashboardAdminQueries
from core.admin_dashboard.schema.donations import DonationsAdminMutations, DonationsAdminQueries
from core.admin_dashboard.schema.help_center import HelpCenterAdminMutations, HelpCenterAdminQueries
from core.admin_dashboard.schema.moderation import ModerationAdminMutations, ModerationAdminQueries
from core.admin_dashboard.schema.users import UsersAdminMutations, UsersAdminQueries


@strawberry.type
class AdminDashboardQueries(
    DashboardAdminQueries,
    UsersAdminQueries,
    CirclesAdminQueries,
    AnchorsAdminQueries,
    ModerationAdminQueries,
    ContactsAdminQueries,
    HelpCenterAdminQueries,
    DonationsAdminQueries,
):
    """Admin dashboard GraphQL queries. All protected by @admin_required."""


@strawberry.type
class AdminDashboardMutations(
    UsersAdminMutations,
    CirclesAdminMutations,
    AnchorsAdminMutations,
    ModerationAdminMutations,
    ContactsAdminMutations,
    HelpCenterAdminMutations,
    DonationsAdminMutations,
    AuthAdminMutations,
):
    """Admin dashboard GraphQL mutations. All protected by @admin_required."""

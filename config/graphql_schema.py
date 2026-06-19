import strawberry
from django.conf import settings
from strawberry.extensions import DisableIntrospection, MaxAliasesLimiter, QueryDepthLimiter
from strawberry.schema import Schema

from core.admin_dashboard.schema import AdminDashboardMutations, AdminDashboardQueries
from core.authentication.schema import AuthMutations, AuthQueries
from core.circles.schema import CircleMutations, CircleQueries
from core.donations.schema import DonationMutations, DonationQueries
from core.engagement.schema import EngagementMutations, EngagementQueries
from core.feed.schema import FeedQueries
from core.follows.schema import FollowMutations, FollowQueries
from core.landing.schema import LandingMutations, LandingQueries
from core.media.schema import MediaMutations, MediaQueries
from core.moderation.schema import ModerationMutations, ModerationQueries
from core.notifications.schema import NotificationMutations, NotificationQueries
from core.posts.schema import PostMutations, PostQueries
from core.profiles.schema import ProfileMutations, ProfileQueries
from core.scripture.schema import ScriptureQueries
from core.shared.graphql_extensions import MaxQueryComplexityLimiter
from core.users.schema import UserMutations, UserQueries


@strawberry.type
class Query(
    AuthQueries,
    FeedQueries,
    FollowQueries,
    ProfileQueries,
    EngagementQueries,
    ModerationQueries,
    NotificationQueries,
    PostQueries,
    ScriptureQueries,
    UserQueries,
    CircleQueries,
    AdminDashboardQueries,
    LandingQueries,
    DonationQueries,
    MediaQueries,
):
    """Root query type — extends all domain queries."""

    pass


@strawberry.type
class Mutation(
    AuthMutations,
    UserMutations,
    MediaMutations,
    PostMutations,
    EngagementMutations,
    FollowMutations,
    ProfileMutations,
    ModerationMutations,
    NotificationMutations,
    CircleMutations,
    AdminDashboardMutations,
    LandingMutations,
    DonationMutations,
):
    """Root mutation type — extends all domain mutations."""

    pass


def build_schema() -> Schema:
    """Construct the GraphQL schema with environment-specific validation guards."""
    extensions = [
        lambda: QueryDepthLimiter(max_depth=settings.GRAPHQL_MAX_DEPTH),
        lambda: MaxAliasesLimiter(max_alias_count=settings.GRAPHQL_MAX_ALIASES),
        lambda: MaxQueryComplexityLimiter(max_complexity=settings.GRAPHQL_MAX_COMPLEXITY),
    ]
    if not settings.GRAPHQL_INTROSPECTION_ENABLED:
        extensions.append(DisableIntrospection)

    return strawberry.Schema(
        query=Query,
        mutation=Mutation,
        extensions=extensions,
    )


schema: Schema = build_schema()

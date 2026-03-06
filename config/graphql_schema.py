import strawberry
from strawberry.schema import Schema

from core.authentication.schema import AuthMutations, AuthQueries
from core.engagement.schema import EngagementMutations, EngagementQueries
from core.feed.schema import FeedQueries
from core.follows.schema import FollowMutations, FollowQueries
from core.media.schema import MediaMutations
from core.moderation.schema import ModerationMutations, ModerationQueries
from core.notifications.schema import NotificationMutations, NotificationQueries
from core.posts.schema import PostMutations
from core.profiles.schema import ProfileMutations, ProfileQueries
from core.scripture.schema import ScriptureQueries
from core.users.schema import UserMutations


@strawberry.type
class Query(
    AuthQueries,
    FeedQueries,
    FollowQueries,
    ProfileQueries,
    EngagementQueries,
    ModerationQueries,
    NotificationQueries,
    ScriptureQueries,
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
):
    """Root mutation type — extends all domain mutations."""

    pass


schema: Schema = strawberry.Schema(
    query=Query,
    mutation=Mutation,
)

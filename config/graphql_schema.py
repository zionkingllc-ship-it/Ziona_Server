import strawberry
from strawberry.schema import Schema

from core.authentication.schema import AuthMutations, AuthQueries
from core.media.schema import MediaMutations
from core.users.schema import UserMutations


@strawberry.type
class Query(AuthQueries):
    """Root query type — extends all domain queries."""

    pass


@strawberry.type
class Mutation(AuthMutations, UserMutations, MediaMutations):
    """Root mutation type — extends all domain mutations."""

    pass


schema: Schema = strawberry.Schema(
    query=Query,
    mutation=Mutation,
)

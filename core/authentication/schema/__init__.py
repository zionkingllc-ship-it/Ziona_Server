"""Authentication GraphQL schema package.

Composes per-concern mutation mixins into the public AuthQueries/AuthMutations
the root schema imports. GraphQL contract unchanged (guarded by
tests/graphql/test_schema_contract.py).
"""

import strawberry

from core.authentication.schema.oauth import OAuthMutations
from core.authentication.schema.onboarding import OnboardingMutations
from core.authentication.schema.otp import OTPMutations
from core.authentication.schema.password import PasswordMutations
from core.authentication.schema.queries import AuthQueries
from core.authentication.schema.register import RegisterMutations
from core.authentication.schema.session import SessionMutations


@strawberry.type
class AuthMutations(
    RegisterMutations,
    SessionMutations,
    PasswordMutations,
    OAuthMutations,
    OTPMutations,
    OnboardingMutations,
):
    """Authentication mutations (composed from per-concern mixins)."""


__all__ = ["AuthMutations", "AuthQueries"]

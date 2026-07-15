"""Auth GraphQL mutations — onboarding.

Split from the former core/authentication/schema.py (no contract change).
"""

import strawberry

from core.authentication.schema.types import (  # noqa: F401
    AddPasswordPayload,
    AuthPayload,
    ChangePasswordPayload,
    GoogleOAuthPayload,
    OTPPayload,
    PasswordResetRequestPayload,
    RegisterPayload,
    VerifyOTPPayload,
    _token_metadata_kwargs,
)
from core.users.schema import AuthenticatedUserType


@strawberry.type
class OnboardingMutations:
    @strawberry.mutation(
        description="Set permanent username after Google OAuth signup. Replaces temporary username (user_XXXXXXXX) with chosen username. Validates availability."
    )
    def finalize_username(
        self,
        info: strawberry.types.Info,
        username: str,
    ) -> AuthPayload:
        """
        Set permanent username inherently after Google OAuth signup natively.

        Replaces the backend-created temporary alias (`user_XXXXXXXXXXXX`) exclusively targeting
        OAuth boundary flows by confirming internal username standards rigorously before
        flipping the `needsUsernameSelection` boolean marker permanently to `false`.

        **Authentication:** Required
        **Parameters:**
        - username (String, required) - Valid string conforming to unique boundary policies natively
        **Returns:** AuthPayload echoing the updated exact user JSON dynamically.
        **Errors:** UNAUTHENTICATED, USERNAME_TAKEN, INVALID_USERNAME_FORMAT， ALREADY_FINALIZED
        """
        from core.authentication.services import AuthService
        from core.authentication.validators import AuthenticationError
        from core.users.schema import _get_authenticated_user_id

        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return AuthPayload(
                success=False,
                message="Authentication required",
                error_code="UNAUTHENTICATED",
            )

        try:
            user = AuthService.finalize_username(
                user_id=user_id,
                username=username,
            )
            return AuthPayload(
                success=True,
                user=AuthenticatedUserType.from_model(user),
            )
        except AuthenticationError as e:
            return AuthPayload(
                success=False,
                message=e.message,
                error_code=e.code,
            )

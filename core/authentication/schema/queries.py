"""Auth GraphQL queries.

Split from the former core/authentication/schema.py (no contract change).
"""

import strawberry


@strawberry.type
class AuthQueries:
    """Authentication domain queries."""

    @strawberry.field(description="Simple health check for the GraphQL endpoint.")
    def health(self) -> str:
        """
        Return a simple health check response.

        Useful for load balancers or uptime monitoring to verify the GraphQL server is responding.

        **Authentication:** Not required
        **Parameters:** None
        **Returns:** String "OK"
        **Errors:** None
        """
        return "OK"

    @strawberry.field(
        description="Generate 4 available username suggestions based on email and optional date of birth. Returns unique, available usernames."
    )
    def suggest_usernames(
        self,
        info: strawberry.types.Info,
        email: str,
        date_of_birth: str | None = None,
        dob: str | None = None,
    ) -> list[str]:
        """
        Generate available username suggestions based on user context.

        Returns exactly 4 unique, available usernames that meet the platform's minimum guidelines.

        **Authentication:** Not required
        **Parameters:**
        - email (String, required) - The user's email address
        - date_of_birth (String, optional) - Date of birth (YYYY-MM-DD)
        - dob (String, optional) - Legacy alias for date_of_birth
        **Returns:** A list of exactly 4 available username strings
        **Errors:** INVALID_EMAIL
        """
        from core.authentication.services import AuthService

        effective_dob = date_of_birth if date_of_birth is not None else dob
        return AuthService.suggest_usernames(email, effective_dob)

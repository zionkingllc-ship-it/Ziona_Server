"""
GraphQL permissions for Ziona Server.

Provides authentication and role-based permission classes
for Strawberry GraphQL resolvers.
"""

import logging
from typing import Any

from strawberry.permission import BasePermission
from strawberry.types import Info

from core.authentication.tokens import TokenError, TokenService
from core.users.models import User

logger = logging.getLogger("core.authentication")


class IsAuthenticated(BasePermission):
    """Permission class that requires a valid JWT access token.

    Extracts the Bearer token from the Authorization header,
    validates it, and attaches the user to the request context.

    Usage:
        @strawberry.mutation(permission_classes=[IsAuthenticated])
        def create_post(self, info: Info) -> Post:
            user = info.context.user
            ...
    """

    message = "Authentication required"

    def has_permission(self, source: Any, info: Info, **kwargs: Any) -> bool:
        """Check if request has a valid access token."""
        request = info.context["request"]
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")

        if not auth_header.startswith("Bearer "):
            return False

        token = auth_header[7:]

        try:
            payload = TokenService.validate_access_token(token)
            # Attach user to context for use in resolvers
            try:
                user = User.objects.get(id=payload["user_id"])
                info.context["user"] = user
                info.context["user_id"] = str(user.id)
                info.context["user_role"] = payload.get("role", "user")
            except User.DoesNotExist:
                return False
            return True
        except TokenError:
            return False


class IsAdmin(BasePermission):
    """Permission class that requires admin role.

    Must be used together with IsAuthenticated.

    Usage:
        @strawberry.mutation(permission_classes=[IsAuthenticated, IsAdmin])
        def ban_user(self, info: Info, user_id: str) -> bool:
            ...
    """

    message = "Admin access required"

    def has_permission(self, source: Any, info: Info, **kwargs: Any) -> bool:
        """Check if authenticated user has admin role."""
        role = info.context.get("user_role")
        return role == "admin"

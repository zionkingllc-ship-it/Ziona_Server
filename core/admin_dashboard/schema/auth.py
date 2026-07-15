"""Admin login.

Split from the former core/admin_dashboard/schema.py (no contract change).
"""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from core.shared.types import ErrorType


@strawberry.type
class AdminLoginPayload:
    """Response for admin login."""

    success: bool
    access_token: str | None = strawberry.field(name="accessToken", default=None)
    refresh_token: str | None = strawberry.field(name="refreshToken", default=None)
    message: str | None = None
    error: ErrorType | None = None


@strawberry.type
class AuthAdminMutations:
    @strawberry.mutation(name="adminLogin", description="Admin login — validates admin role.")
    def admin_login(self, info: Info, email: str, password: str) -> AdminLoginPayload:
        from core.admin_dashboard.permissions import _get_client_ip, log_admin_action
        from core.authentication.services import AuthService
        from core.shared.exceptions import ZionaError

        request = info.context.request
        ip = _get_client_ip(request)

        try:
            result = AuthService.login(
                email=email,
                password=password,
                ip_address=ip,
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
            )
        except ZionaError as e:
            return AdminLoginPayload(
                success=False,
                message=e.message,
                error=ErrorType(code=e.code, message=e.message),
            )

        user = result["user"]
        if not user.is_admin:
            return AdminLoginPayload(
                success=False,
                message="Admin access required.",
                error=ErrorType(code="NOT_AUTHORIZED", message="Admin access required."),
            )

        # Log successful admin login
        import contextlib

        with contextlib.suppress(Exception):
            log_admin_action(
                admin_user=user,
                action="ADMIN_LOGIN",
                target_type="User",
                target_id=str(user.id),
                ip_address=ip,
            )

        return AdminLoginPayload(
            success=True,
            access_token=result["access_token"],
            refresh_token=result["refresh_token"],
        )

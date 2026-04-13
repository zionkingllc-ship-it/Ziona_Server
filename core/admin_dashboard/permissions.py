"""
Admin permission decorator for GraphQL resolvers.

Validates JWT contains admin role, confirms user.is_admin in the database,
and logs unauthorized access attempts to the audit trail.
"""

import functools
import logging

from strawberry.types import Info

logger = logging.getLogger("core.admin_dashboard")


def _get_client_ip(request) -> str:
    """Extract client IP from request, handling proxies."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


def get_admin_user(info: Info):
    """Extract and validate admin user from GraphQL context.

    Returns:
        User instance if authenticated admin, None otherwise.
    """
    from core.authentication.tokens import TokenService
    from core.users.models import User

    request = info.context["request"]
    auth_header = request.META.get("HTTP_AUTHORIZATION", "")

    if not auth_header.startswith("Bearer "):
        return None

    token = auth_header[7:]

    try:
        payload = TokenService.validate_access_token(token)
    except Exception:
        return None

    if payload.get("role") != "admin":
        return None

    try:
        user = User.objects.get(
            id=payload["user_id"],
            deleted_at__isnull=True,
        )
    except User.DoesNotExist:
        return None

    if not user.is_admin or not user.is_active:
        return None

    return user


def admin_required(func):
    """Decorator that enforces admin-only access on GraphQL resolvers.

    - Validates JWT has admin role
    - Confirms user.is_admin in database
    - Logs unauthorized access attempts to AdminAuditLog
    - Injects admin_user into info.context for downstream use

    Usage:
        @strawberry.mutation
        @admin_required
        def my_admin_mutation(self, info, ...) -> SomePayload:
            admin_user = info.context["admin_user"]
            ...
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # Find the `info` argument (can be positional or keyword)
        info = kwargs.get("info")
        if info is None:
            for arg in args:
                if isinstance(arg, Info):
                    info = arg
                    break

        if info is None:
            logger.error("admin_required: could not find Info argument")
            return _build_error_response(
                func,
                code="INTERNAL_ERROR",
                message="Internal server error",
            )

        request = info.context["request"]
        ip_address = _get_client_ip(request)
        admin_user = get_admin_user(info)

        if admin_user is None:
            # Log unauthorized access attempt
            _log_unauthorized_attempt(info, ip_address, func.__name__)

            return _build_error_response(
                func,
                code="NOT_AUTHORIZED",
                message="Admin access required",
            )

        # Inject admin_user into context for downstream use
        info.context["admin_user"] = admin_user
        info.context["admin_ip"] = ip_address

        return func(*args, **kwargs)

    return wrapper


def _log_unauthorized_attempt(info: Info, ip_address: str, endpoint_name: str):
    """Log unauthorized admin access attempt to audit log."""
    try:
        from core.admin_dashboard.models import AdminAuditLog
        from core.authentication.tokens import TokenService

        # Try to identify who attempted access
        request = info.context["request"]
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        user_id = None

        import contextlib

        if auth_header.startswith("Bearer "):
            with contextlib.suppress(Exception):
                payload = TokenService.validate_access_token(auth_header[7:])
                user_id = payload.get("user_id")

        AdminAuditLog.objects.create(
            admin_user_id=user_id,
            action="UNAUTHORIZED_ACCESS_ATTEMPT",
            target_type="AdminEndpoint",
            target_id=endpoint_name,
            details={"attempted_endpoint": endpoint_name},
            ip_address=ip_address,
        )
    except Exception:
        logger.warning("Failed to log unauthorized access attempt", exc_info=True)


def _build_error_response(func, code: str, message: str):
    """Build a generic error response by inspecting the return type annotation."""
    from core.shared.types import ErrorType

    # Try to construct the return type with error fields
    return_type = func.__annotations__.get("return")
    import contextlib

    if return_type:
        with contextlib.suppress(Exception):
            return return_type(
                success=False,
                error=ErrorType(code=code, message=message),
            )

    # Fallback: raise PermissionError which Strawberry converts to a GraphQL error
    raise PermissionError(message)


def log_admin_action(
    admin_user,
    action: str,
    target_type: str,
    target_id: str,
    details: dict | None = None,
    ip_address: str = "",
) -> None:
    """Helper to create an audit log entry inside the current transaction.

    This should be called from within an @transaction.atomic() block
    so the audit log and the action are committed or rolled back together.
    """
    from core.admin_dashboard.models import AdminAuditLog

    AdminAuditLog.objects.create(
        admin_user=admin_user,
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        details=details or {},
        ip_address=ip_address,
    )

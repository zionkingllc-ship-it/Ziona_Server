import json
import logging
import threading
import time
import traceback
import uuid
from collections import OrderedDict

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse

from core.shared.request_utils import get_client_ip

logger = logging.getLogger("core.middleware")


# ---------------------------------------------------------------------------
# In-process IP block cache
# ---------------------------------------------------------------------------
# A small thread-safe OrderedDict that caches recently-blocked IPs for up to
# _IP_BLOCK_CACHE_TTL seconds. When an IP is rate-limited, we store it here
# so that subsequent requests from that IP within the same process can be
# rejected without issuing any Redis command at all.
#
# Multi-worker safety note:
#   This cache lives in process memory, so each Gunicorn worker maintains its
#   own independent copy.  This is intentional and safe because:
#     • The Redis sorted-set counter (shared state) is the authoritative source
#       of truth for *whether* an IP is blocked.
#     • This cache only avoids the Redis round-trip for IPs this worker has
#       ALREADY confirmed to be blocked via Redis.
#     • Worst case: another worker blocks an IP that this worker has not yet
#       seen — this worker performs one extra Redis call before learning about
#       the block.  That is acceptable behaviour.
# ---------------------------------------------------------------------------
_ip_block_lock = threading.Lock()
_ip_block_cache: OrderedDict = OrderedDict()
_IP_BLOCK_CACHE_MAX = 2000  # Maximum IPs to track per worker
_IP_BLOCK_CACHE_TTL = 10  # Seconds to trust a cached block decision


def _check_ip_blocked(ip: str) -> bool:
    """Return True if this IP is known-blocked in this worker's local cache."""
    now = time.monotonic()
    with _ip_block_lock:
        exp = _ip_block_cache.get(ip)
        if exp is None:
            return False
        if exp > now:
            return True
        del _ip_block_cache[ip]
        return False


def _mark_ip_blocked(ip: str, ttl: int) -> None:
    """Cache an IP as blocked for up to _IP_BLOCK_CACHE_TTL seconds."""
    now = time.monotonic()
    with _ip_block_lock:
        if len(_ip_block_cache) >= _IP_BLOCK_CACHE_MAX:
            _ip_block_cache.popitem(last=False)  # Evict oldest entry (LRU)
        _ip_block_cache[ip] = now + min(ttl, _IP_BLOCK_CACHE_TTL)


class StructuredLoggingMiddleware:
    """Add trace ID and request context to all log records.

    Generates a unique trace_id per request and attaches it
    to all log records for request correlation.
    """

    def __init__(self, get_response):
        """Initialize middleware."""
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Process request, adding trace context."""
        trace_id = str(uuid.uuid4())[:8]
        request.trace_id = trace_id

        start_time = time.monotonic()

        response = self.get_response(request)

        duration_ms = (time.monotonic() - start_time) * 1000

        logger.info(
            "request_completed",
            extra={
                "trace_id": trace_id,
                "method": request.method,
                "path": request.path,
                "status_code": response.status_code,
                "duration_ms": round(duration_ms, 2),
                "ip_address": _get_client_ip(request),
                "user_agent": request.META.get("HTTP_USER_AGENT", ""),
            },
        )

        response["X-Trace-ID"] = trace_id
        return response


class RateLimitMiddleware:
    """Redis-based sliding window rate limiting.

    Enforces per-IP and per-user rate limits based on endpoint type.
    Returns 429 when limits are exceeded.

    Uses an atomic Lua script to perform the entire sliding-window check
    (ZREMRANGEBYSCORE + ZCARD + ZADD + EXPIRE) in a single Redis command,
    reducing Upstash request consumption by 75% vs. a manual pipeline.

    An in-process IP block cache provides a zero-Redis fast path for IPs
    that this worker already knows are rate-limited.

    Rate limits are configurable via Django settings:
    - RATE_LIMIT_LOGIN: Login endpoint limit
    - RATE_LIMIT_REGISTER: Registration endpoint limit
    - RATE_LIMIT_CHECK_EMAIL: Email check endpoint limit
    - RATE_LIMIT_MUTATIONS: GraphQL mutation limit
    - RATE_LIMIT_QUERIES: GraphQL query limit
    """

    RATE_LIMITED_PATHS = {
        "/api/auth/login": "RATE_LIMIT_LOGIN",
        "/api/auth/register": "RATE_LIMIT_REGISTER",
        "/api/auth/check-email": "RATE_LIMIT_CHECK_EMAIL",
        "/api/auth/password-reset": "RATE_LIMIT_PASSWORD_RESET",
        "/api/payments/support-once": "RATE_LIMIT_SUPPORT_CHECKOUT",
        "/api/payments/support-monthly": "RATE_LIMIT_SUPPORT_CHECKOUT",
    }

    def __init__(self, get_response):
        """Initialize middleware."""
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Check rate limits before processing request."""
        if not getattr(settings, "RATE_LIMIT_ENABLED", True):
            return self.get_response(request)

        ip = _get_client_ip(request)

        # --- Fast path: IP is already known-blocked in this worker (0 Redis commands) ---
        if _check_ip_blocked(ip):
            return self._rate_limit_response(1)

        from core.shared.redis_lua import LuaLimiter

        # --- Auth endpoint rate limits (per IP) ---
        path = request.path.rstrip("/")
        for limited_path, config_key in self.RATE_LIMITED_PATHS.items():
            if path == limited_path.rstrip("/"):
                limit_str = getattr(settings, config_key, "10/60s")
                max_requests, window_seconds = _parse_rate_limit(limit_str)
                key = f"ratelimit:{limited_path}:{ip}"

                is_limited, retry_after = LuaLimiter.check_rate_limit(
                    key, max_requests, window_seconds
                )
                if is_limited:
                    _mark_ip_blocked(ip, retry_after)
                    return self._rate_limit_response(retry_after)
                break  # Path matched — stop checking other paths

        # --- GraphQL operation rate limit (per user/IP) ---
        if request.path.rstrip("/") == "/graphql" and request.method == "POST":
            user_id = getattr(request, "user_id", None) or ip
            operation_kind = _graphql_operation_kind(request)
            setting_name = (
                "RATE_LIMIT_MUTATIONS" if operation_kind == "mutation" else "RATE_LIMIT_QUERIES"
            )
            default_limit = "30/60s" if operation_kind == "mutation" else "100/60s"
            limit_str = getattr(settings, setting_name, default_limit)
            max_requests, window_seconds = _parse_rate_limit(limit_str)
            key = f"ratelimit:graphql:{operation_kind}:{user_id}"

            is_limited, retry_after = LuaLimiter.check_rate_limit(key, max_requests, window_seconds)
            if is_limited:
                return self._rate_limit_response(retry_after)

        return self.get_response(request)

    def _rate_limit_response(self, retry_after: int) -> JsonResponse:
        """Return a 429 Too Many Requests response with a human-readable wait time."""
        user_message = f"Too many requests. Please try again in {_format_retry_after(retry_after)}."
        response = JsonResponse(
            {
                "success": False,
                "retryAfter": retry_after,  # raw seconds for the client countdown timer
                "userMessage": user_message,
                "error": {
                    "code": "RATE_LIMIT_EXCEEDED",
                    "message": user_message,
                    "details": {
                        "retryAfter": retry_after,  # raw seconds for programmatic use
                    },
                },
            },
            status=429,
        )
        response["Retry-After"] = str(retry_after)
        return response


def _graphql_operation_kind(request: HttpRequest) -> str:
    """Return query or mutation for a GraphQL POST without consuming its body."""
    try:
        from graphql import OperationDefinitionNode, parse

        payload = json.loads(request.body or b"{}")
        query = payload.get("query", "") if isinstance(payload, dict) else ""
        operation_name = payload.get("operationName") if isinstance(payload, dict) else None
        document = parse(query)
        operations = [
            definition
            for definition in document.definitions
            if isinstance(definition, OperationDefinitionNode)
        ]
        if operation_name:
            operations = [
                operation
                for operation in operations
                if operation.name and operation.name.value == operation_name
            ]
        if len(operations) == 1:
            return operations[0].operation.value
    except Exception:
        logger.debug("Unable to classify GraphQL operation for rate limiting", exc_info=True)

    # Conservative fallback: unknown writes receive the stricter mutation limit.
    return "mutation"


def _get_client_ip(request: HttpRequest) -> str:
    """Delegate client IP extraction to the shared trusted-proxy helper."""
    return get_client_ip(request)


def _format_retry_after(seconds: int) -> str:
    """Human-readable wait duration for rate-limit messages.

    The raw integer `retryAfter` and the `Retry-After` header still carry the
    exact seconds; this is only for the user-facing message text.
    """
    if seconds < 60:
        return "a few seconds"
    if seconds < 120:
        return "about a minute"
    if seconds < 3600:
        return f"about {round(seconds / 60)} minutes"
    hours = round(seconds / 3600)
    return f"about {hours} hour{'s' if hours != 1 else ''}"


def _parse_rate_limit(limit_str: str) -> tuple[int, int]:
    """Parse a rate limit string into (max_requests, window_seconds).

    Supported formats:
    - '5/15m' → 5 requests per 15 minutes
    - '30/60s' → 30 requests per 60 seconds
    - '100/1h' → 100 requests per 1 hour

    Args:
        limit_str: Rate limit string.

    Returns:
        Tuple of (max_requests, window_seconds).
    """
    parts = limit_str.split("/")
    max_requests = int(parts[0])
    window = parts[1]

    if window.endswith("s"):
        window_seconds = int(window[:-1])
    elif window.endswith("m"):
        window_seconds = int(window[:-1]) * 60
    elif window.endswith("h"):
        window_seconds = int(window[:-1]) * 3600
    else:
        window_seconds = int(window)

    return max_requests, window_seconds


class GlobalExceptionMiddleware:
    """Middleware to catch all unhandled exceptions and return standardized JSON."""

    def __init__(self, get_response):
        """Initialize middleware."""
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Process request."""
        return self.get_response(request)

    def process_exception(self, request: HttpRequest, exception: Exception) -> JsonResponse:
        """Handle uncaught exceptions."""
        is_authenticated = hasattr(request, "user") and request.user.is_authenticated
        user_id = request.user.id if is_authenticated else None

        logger.error(
            "Unhandled exception in request",
            exc_info=True,
            extra={
                "path": request.path,
                "method": request.method,
                "user_id": user_id,
                "exception_type": type(exception).__name__,
            },
        )

        error_detail = {
            "code": "INTERNAL_SERVER_ERROR",
            "message": "An internal error occurred. Please try again later.",
        }

        if settings.DEBUG:
            error_detail["message"] = str(exception)
            error_detail["type"] = type(exception).__name__
            error_detail["traceback"] = traceback.format_tb(exception.__traceback__)

        return JsonResponse(
            {
                "success": False,
                "error": error_detail,
            },
            status=500,
        )

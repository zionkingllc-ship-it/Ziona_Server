import logging
import time
import uuid

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse

logger = logging.getLogger("core.middleware")


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

    Enforces per-IP and per-user rate limits based on
    endpoint type. Returns 429 when limits are exceeded.

    Rate limits are configurable via Django settings:
    - RATE_LIMIT_LOGIN: Login endpoint limit
    - RATE_LIMIT_REGISTER: Registration endpoint limit
    - RATE_LIMIT_MUTATIONS: GraphQL mutation limit
    - RATE_LIMIT_QUERIES: GraphQL query limit
    """

    RATE_LIMITED_PATHS = {
        "/api/auth/login": "RATE_LIMIT_LOGIN",
        "/api/auth/register": "RATE_LIMIT_REGISTER",
        "/api/auth/password-reset": "RATE_LIMIT_PASSWORD_RESET",
    }

    def __init__(self, get_response):
        """Initialize middleware."""
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Check rate limits before processing request."""
        if not getattr(settings, "RATE_LIMIT_ENABLED", True):
            return self.get_response(request)

        path = request.path.rstrip("/")
        for limited_path, config_key in self.RATE_LIMITED_PATHS.items():
            if path == limited_path.rstrip("/"):
                limit_str = getattr(settings, config_key, "10/60s")
                identifier = _get_client_ip(request)
                is_limited, retry_after = self._check_rate_limit(
                    f"ratelimit:{limited_path}:{identifier}",
                    limit_str,
                )
                if is_limited:
                    return self._rate_limit_response(retry_after)

        if request.path.rstrip("/") == "/graphql" and request.method == "POST":
            user_id = getattr(request, "user_id", None) or _get_client_ip(request)
            limit_str = getattr(settings, "RATE_LIMIT_MUTATIONS", "30/60s")
            is_limited, retry_after = self._check_rate_limit(
                f"ratelimit:graphql:{user_id}",
                limit_str,
            )
            if is_limited:
                return self._rate_limit_response(retry_after)

        return self.get_response(request)

    def _check_rate_limit(self, key: str, limit_str: str) -> tuple[bool, int]:
        """Check if a rate limit has been exceeded using Redis.

        Args:
            key: Redis key for this rate limit counter.
            limit_str: Rate limit string (e.g., '5/15m').

        Returns:
            Tuple of (is_limited, retry_after_seconds).
        """
        try:
            from django_redis import get_redis_connection

            max_requests, window_seconds = _parse_rate_limit(limit_str)
            redis_conn = get_redis_connection("default")

            now = time.time()
            window_start = now - window_seconds

            pipeline = redis_conn.pipeline()
            pipeline.zremrangebyscore(key, 0, window_start)
            pipeline.zcard(key)
            pipeline.zadd(key, {str(now): now})
            pipeline.expire(key, window_seconds)
            results = pipeline.execute()

            current_count = results[1]
            if current_count >= max_requests:
                oldest = redis_conn.zrange(key, 0, 0, withscores=True)
                if oldest:
                    retry_after = int(oldest[0][1] + window_seconds - now) + 1
                    return True, max(retry_after, 1)
                return True, window_seconds

            return False, 0

        except Exception:
            logger.warning("Rate limiting unavailable - Redis connection failed")
            return False, 0

    def _rate_limit_response(self, retry_after: int) -> JsonResponse:
        """Return a 429 Too Many Requests response."""
        response = JsonResponse(
            {
                "success": False,
                "error": {
                    "code": "RATE_LIMIT_EXCEEDED",
                    "message": f"Rate limit exceeded. Try again in {retry_after} seconds.",
                },
            },
            status=429,
        )
        response["Retry-After"] = str(retry_after)
        return response


def _get_client_ip(request: HttpRequest) -> str:
    """Extract the client IP address from request headers.

    Handles X-Forwarded-For from proxies/load balancers.

    Args:
        request: The incoming HTTP request.

    Returns:
        Client IP address as a string.
    """
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


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

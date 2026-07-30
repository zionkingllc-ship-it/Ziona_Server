import json
import logging
import traceback
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    """Format log records as JSON for structured logging.

    Output format:
    {
        "timestamp": "2026-02-12T14:30:00Z",
        "level": "INFO",
        "logger": "core.authentication",
        "message": "User logged in",
        "module": "services",
        "function": "login",
        "trace_id": "abc-123",
        "extra": { ... }
    }
    """

    def format(self, record: logging.LogRecord) -> str:
        """Format a log record as a JSON string."""
        log_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        if hasattr(record, "trace_id"):
            log_data["trace_id"] = record.trace_id

        if record.exc_info and record.exc_info[0] is not None:
            log_data["exception"] = {
                "type": record.exc_info[0].__name__,
                "message": str(record.exc_info[1]),
                "traceback": traceback.format_exception(*record.exc_info),
            }

        extra_fields = {}
        for key, value in record.__dict__.items():
            if key not in (
                "name",
                "msg",
                "args",
                "levelname",
                "levelno",
                "pathname",
                "filename",
                "module",
                "exc_info",
                "exc_text",
                "stack_info",
                "lineno",
                "funcName",
                "created",
                "msecs",
                "relativeCreated",
                "thread",
                "threadName",
                "processName",
                "process",
                "message",
                "trace_id",
                "taskName",
            ):
                try:
                    json.dumps(value)
                    extra_fields[key] = value
                except (TypeError, ValueError):
                    extra_fields[key] = str(value)

        if extra_fields:
            log_data["extra"] = extra_fields

        return json.dumps(log_data, default=str)


def mask_email(email: str) -> str:
    """Mask an email address for safe logging.

    Args:
        email: Full email address.

    Returns:
        Masked email, e.g. 'u***@example.com'.
    """
    if not email or "@" not in email:
        return "***"
    local, domain = email.split("@", 1)
    return f"{local[0]}***@{domain}"


def get_audit_logger() -> logging.Logger:
    """Get the audit logger for security events.

    Returns:
        Logger configured for audit events.
    """
    return logging.getLogger("core.audit")


def log_security_event(
    event_type: str,
    user_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    metadata: dict | None = None,
) -> None:
    """Log a security event in structured format.

    Args:
        event_type: Type of security event (e.g., 'auth.login.success').
        user_id: UUID of the user involved, if applicable.
        ip_address: IP address of the request.
        user_agent: User-Agent header from the request.
        metadata: Additional event-specific data.
    """
    logger = get_audit_logger()
    logger.info(
        event_type,
        extra={
            "event_type": event_type,
            "user_id": str(user_id) if user_id else None,
            "ip_address": ip_address,
            "user_agent": user_agent,
            "metadata": metadata or {},
        },
    )


def log_rejected_mutation(info, *, operation: str, error, **context) -> None:
    """Log a handled GraphQL mutation rejection so it is visible in logs.

    A handled domain error (a ``ZionaError`` mapped to a ``success: false``
    payload) returns HTTP 200, so it never surfaces in the request log or in
    Sentry. This records the error ``code``, the request ``trace_id``, and any
    operation-specific context (e.g. the reported ``targetType``) at WARNING so
    failures like ``INVALID_TARGET_TYPE`` are debuggable from the server log
    instead of guessed.

    Args:
        info: Strawberry resolver ``Info`` (used to read the request trace_id).
        operation: GraphQL mutation name, e.g. ``"reportCircleContent"``.
        error: The raised ``ZionaError`` — its ``code``/``message`` are logged.
        **context: Extra scalar fields to log, e.g. ``target_type="POST"``.
            Avoid keys reserved by ``LogRecord`` (e.g. ``message``, ``module``).
    """
    request = getattr(getattr(info, "context", None), "request", None)
    trace_id = getattr(request, "trace_id", None)
    logging.getLogger("core.graphql").warning(
        "graphql_mutation_rejected",
        extra={
            "operation": operation,
            "code": getattr(error, "code", None),
            "error_message": getattr(error, "message", None),
            "trace_id": trace_id,
            **context,
        },
    )

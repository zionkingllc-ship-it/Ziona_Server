"""Handled circle-mutation rejections must be visible in the server log.

A ``ZionaError`` mapped to a ``success: false`` payload returns HTTP 200, so it
is invisible in both the request log and Sentry. ``log_rejected_mutation``
records the error code, the request ``trace_id``, and operation-specific context
(e.g. the reported ``targetType``) at WARNING so a failure like
``INVALID_TARGET_TYPE`` is debuggable from logs instead of guessed.
"""

import logging
from types import SimpleNamespace

from core.shared.exceptions import ZionaError
from core.shared.logging import log_rejected_mutation


def _fake_info(trace_id):
    """A minimal stand-in for the Strawberry Info the resolver reads trace_id from."""
    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(trace_id=trace_id)))


class _CaptureHandler(logging.Handler):
    """Collects records emitted on a specific logger.

    `core.graphql` propagates to `core` (which has propagate=False), so pytest's
    root-level caplog never sees these records — attach directly to the logger.
    """

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


def _attach(logger_name):
    logger = logging.getLogger(logger_name)
    handler = _CaptureHandler()
    logger.addHandler(handler)
    return logger, handler


def test_log_rejected_mutation_emits_structured_record():
    logger, handler = _attach("core.graphql")
    try:
        log_rejected_mutation(
            _fake_info("abc12345"),
            operation="reportCircleContent",
            error=ZionaError("Invalid target type", code="INVALID_TARGET_TYPE"),
            target_type="POST",
            circle_id="circle-1",
        )
    finally:
        logger.removeHandler(handler)

    assert len(handler.records) == 1
    record = handler.records[0]
    assert record.levelno == logging.WARNING
    assert record.getMessage() == "graphql_mutation_rejected"
    assert record.operation == "reportCircleContent"
    assert record.code == "INVALID_TARGET_TYPE"
    assert record.error_message == "Invalid target type"
    assert record.trace_id == "abc12345"
    assert record.target_type == "POST"
    assert record.circle_id == "circle-1"


def test_report_circle_content_rejection_is_logged(monkeypatch):
    import core.circles.moderation_services as moderation_services
    import core.circles.schema.mutations as mutations

    monkeypatch.setattr(mutations, "_get_authenticated_user_id", lambda info: "user-123")

    def _raise(**kwargs):
        raise ZionaError("Invalid target type", code="INVALID_TARGET_TYPE")

    monkeypatch.setattr(moderation_services, "report_circle_content", _raise)

    logger, handler = _attach("core.graphql")
    try:
        payload = mutations.CircleMutations().report_circle_content(
            _fake_info("trace-xyz"),
            circle_id="circle-1",
            target_type="CIRCLE_POST",
            target_id="target-1",
            reason="spam",
        )
    finally:
        logger.removeHandler(handler)

    # Contract unchanged: the resolver still returns a structured success:false payload.
    assert payload.success is False
    assert payload.error.code == "INVALID_TARGET_TYPE"

    # …and now the rejection is also visible in the log, with the reported target type.
    assert len(handler.records) == 1
    record = handler.records[0]
    assert record.getMessage() == "graphql_mutation_rejected"
    assert record.operation == "reportCircleContent"
    assert record.code == "INVALID_TARGET_TYPE"
    assert record.target_type == "CIRCLE_POST"
    assert record.trace_id == "trace-xyz"

"""429 rate-limit responses: human-readable wait time, machine-readable retryAfter."""

import json

from core.shared.middleware import RateLimitMiddleware, _format_retry_after


def test_format_retry_after_is_human_readable():
    assert _format_retry_after(45) == "a few seconds"
    assert _format_retry_after(90) == "about a minute"
    assert _format_retry_after(2873) == "about 48 minutes"
    assert _format_retry_after(3600) == "about 1 hour"
    assert _format_retry_after(7200) == "about 2 hours"


def test_429_message_is_human_but_retry_after_stays_raw_seconds():
    mw = RateLimitMiddleware(lambda _request: None)
    response = mw._rate_limit_response(2873)

    assert response.status_code == 429
    body = json.loads(response.content)

    # User-facing text is human-readable, never raw seconds.
    assert "about 48 minutes" in body["userMessage"]
    assert "about 48 minutes" in body["error"]["message"]
    assert "2873" not in body["userMessage"]

    # Machine-readable seconds preserved for the client countdown + programmatic use.
    assert body["retryAfter"] == 2873
    assert isinstance(body["retryAfter"], int)
    assert body["error"]["details"]["retryAfter"] == 2873
    assert body["error"]["code"] == "RATE_LIMIT_EXCEEDED"
    assert response["Retry-After"] == "2873"

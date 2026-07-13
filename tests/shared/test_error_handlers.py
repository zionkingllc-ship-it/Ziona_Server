"""Tests for the project-level JSON error handlers (config/urls.py)."""

import json

from django.test import RequestFactory

from config.urls import custom_400_handler


def test_custom_400_handler_returns_json_envelope():
    """Fix #4: DisallowedHost/BadRequest are raised by Django's core handler
    before the JSON middleware runs. Without a JSON handler400 they render an
    HTML page, which breaks JSON-only clients ("Unexpected character: <").
    This handler must return the standardized JSON envelope with a 400 status.
    """
    request = RequestFactory().get("/api/auth/apple")
    response = custom_400_handler(request, exception=Exception("Invalid HTTP_HOST header"))

    assert response.status_code == 400
    assert response["Content-Type"] == "application/json"

    body = json.loads(response.content)
    assert body["success"] is False
    assert body["error"]["code"] == "BAD_REQUEST"
    assert body["error"]["path"] == "/api/auth/apple"

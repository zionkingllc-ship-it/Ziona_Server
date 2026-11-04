import json
from unittest.mock import patch

import pytest

from core.users.models import User


@pytest.mark.django_db
def test_signup_queue_failure_can_recover_by_resending(client):
    payload = {
        "email": "queue-recovery@example.com",
        "password": "SecureP@ss1",  # pragma: allowlist secret
        "username": "queue_recovery",
        "date_of_birth": "2000-01-15",
    }
    with (
        patch(
            "core.shared.tasks.email_tasks.queue_email_delivery",
            side_effect=RuntimeError("broker unavailable"),
        ),
        patch("core.authentication.services.log_security_event") as audit,
    ):
        response = client.post(
            "/api/auth/register", data=json.dumps(payload), content_type="application/json"
        )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "OTP_EMAIL_QUEUE_FAILED"
    assert response.json()["success"] is False
    user = User.objects.get(email=payload["email"])
    assert not user.is_email_verified
    events = [call.args[0] for call in audit.call_args_list]
    assert "auth.register.created" in events
    assert "auth.register.verification_unavailable" in events
    assert "auth.register.success" not in events

    with patch("core.shared.tasks.email_tasks.queue_email_delivery") as email:
        response = client.post(
            "/api/auth/resend-otp",
            data=json.dumps({"email": payload["email"]}),
            content_type="application/json",
        )
    assert response.status_code == 200
    email.assert_called_once()
    assert User.objects.get(email=payload["email"]).pk == user.pk
    assert response.json()["data"]["expiresIn"] == 600


def test_queue_failure_status_consistent_across_response_helpers():
    from core.authentication.response_helpers import error_response

    assert error_response("Unable to queue code", "OTP_EMAIL_QUEUE_FAILED").status_code == 503


@pytest.mark.django_db
def test_signup_success_audit_requires_successful_enqueue(client):
    with (
        patch("core.shared.tasks.email_tasks.queue_email_delivery") as queue,
        patch("core.authentication.services.log_security_event") as audit,
    ):
        response = client.post(
            "/api/auth/register",
            data=json.dumps(
                {
                    "email": "queued@example.com",
                    "password": "SecureP@ss1",  # pragma: allowlist secret
                    "username": "queued_user",
                    "date_of_birth": "2000-01-15",
                }
            ),
            content_type="application/json",
        )
    assert response.status_code == 201
    queue.assert_called_once()
    events = [call.args[0] for call in audit.call_args_list]
    assert events == ["auth.register.created", "auth.register.success"]

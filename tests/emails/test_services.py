from unittest.mock import patch

from core.emails.services import EmailService


def test_verify_email_queues_with_real_otp_expiry_copy(settings):
    settings.DEFAULT_FROM_EMAIL = "noreply@ziona.app"

    with patch("core.shared.tasks.email_tasks.queue_email_delivery") as mock_queue:
        result = EmailService.send_verify_email("Brian", "brian@example.com", "627702")

    assert result is True
    mock_queue.assert_called_once()
    kwargs = mock_queue.call_args.kwargs
    assert kwargs["subject"] == "Verify your Ziona account"
    assert kwargs["from_email"] == "noreply@ziona.app"
    assert kwargs["recipient_list"] == ["brian@example.com"]
    assert "10 minutes" in kwargs["message"]
    assert "10 minutes" in kwargs["html_message"]
    assert kwargs["email_kind"] == "verify_email"


def test_verify_email_reports_queue_failure(settings):
    settings.DEFAULT_FROM_EMAIL = "noreply@ziona.app"

    with patch(
        "core.shared.tasks.email_tasks.queue_email_delivery",
        side_effect=RuntimeError("broker unavailable"),
    ):
        result = EmailService.send_verify_email("Brian", "brian@example.com", "627702")

    assert result is False

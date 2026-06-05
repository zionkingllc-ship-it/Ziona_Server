from unittest.mock import patch

from core.emails.services import EmailService


def test_verify_email_queues_with_real_otp_expiry_copy(settings):
    settings.DEFAULT_FROM_EMAIL = "noreply@ziona.app"

    with patch("core.shared.tasks.email_tasks.send_email_async.delay") as mock_delay:
        result = EmailService.send_verify_email("Brian", "brian@example.com", "627702")

    assert result is True
    mock_delay.assert_called_once()
    kwargs = mock_delay.call_args.kwargs
    assert kwargs["subject"] == "Verify your Ziona account"
    assert kwargs["from_email"] == "noreply@ziona.app"
    assert kwargs["recipient_list"] == ["brian@example.com"]
    assert "10 minutes" in kwargs["message"]
    assert "10 minutes" in kwargs["html_message"]


def test_verify_email_reports_queue_failure(settings):
    settings.DEFAULT_FROM_EMAIL = "noreply@ziona.app"

    with patch(
        "core.shared.tasks.email_tasks.send_email_async.delay",
        side_effect=RuntimeError("broker unavailable"),
    ):
        result = EmailService.send_verify_email("Brian", "brian@example.com", "627702")

    assert result is False

from unittest.mock import MagicMock, patch

import pytest
from celery.exceptions import MaxRetriesExceededError

from core.shared.tasks.email_tasks import send_email_async


@pytest.mark.django_db
class TestEmailTasks:
    """Test suite for asynchronous email sending via Celery."""

    @patch("core.shared.tasks.email_tasks.send_mail")
    def test_send_email_success(self, mock_send_mail):
        """Test successful email sending."""
        mock_send_mail.return_value = 1
        subject = "Test Subject"
        message = "Test message body"
        from_email = "noreply@ziona.app"
        recipients = ["test@example.com"]

        result = send_email_async(
            subject=subject,
            message=message,
            from_email=from_email,
            recipient_list=recipients,
        )

        mock_send_mail.assert_called_once_with(
            subject=subject,
            message=message,
            from_email=from_email,
            recipient_list=recipients,
            fail_silently=False,
        )
        assert result["success"] is True
        assert result["message"] == "Email sent"

    @patch("core.shared.tasks.email_tasks.send_mail")
    def test_send_email_retry_on_failure(self, mock_send_mail):
        """Test email task retries on failure."""
        mock_send_mail.side_effect = Exception("SMTP connection failed")
        mock_task = MagicMock()
        mock_task.request.retries = 0
        mock_task.request.id = "test-task-123"

        with patch.object(send_email_async, "retry") as mock_retry:
            mock_retry.side_effect = Exception("Retry triggered")

            with pytest.raises(Exception, match="Retry triggered"):
                send_email_async(
                    subject="Test",
                    message="Body",
                    from_email="test@ziona.app",
                    recipient_list=["user@example.com"],
                )

    @patch("core.shared.tasks.email_tasks.send_mail")
    @patch("core.shared.tasks.email_tasks.logger")
    def test_send_email_max_retries_logs_error(self, mock_logger, mock_send_mail):
        """Test email task logs to Sentry after max retries."""
        mock_send_mail.side_effect = Exception("Permanent failure")

        with patch.object(send_email_async, "retry") as mock_retry:
            mock_retry.side_effect = MaxRetriesExceededError("Max retries exceeded")

            result = send_email_async(
                subject="Test",
                message="Body",
                from_email="test@ziona.app",
                recipient_list=["user@example.com"],
            )

            assert result["success"] is False
            assert "Failed after retries" in result["message"]
            mock_logger.error.assert_called()

    @patch("core.shared.tasks.email_tasks.send_mail")
    def test_send_email_retries_when_backend_sends_zero_messages(self, mock_send_mail):
        """A backend return value of 0 should be treated as a delivery failure."""
        mock_send_mail.return_value = 0

        with patch.object(send_email_async, "retry") as mock_retry:
            mock_retry.side_effect = Exception("Retry triggered")

            with pytest.raises(Exception, match="Retry triggered"):
                send_email_async(
                    subject="Test",
                    message="Body",
                    from_email="test@ziona.app",
                    recipient_list=["user@example.com"],
                )

    def test_task_queuing_non_blocking(self):
        """Test that .delay() returns immediately without blocking."""
        import time

        start = time.time()
        task = send_email_async.delay(
            subject="Test",
            message="Body",
            from_email="noreply@ziona.app",
            recipient_list=["test@example.com"],
        )
        elapsed = time.time() - start

        assert elapsed < 0.1
        assert task.id is not None

    @patch("core.shared.tasks.email_tasks.send_mail")
    def test_uses_default_from_email_when_none(self, mock_send_mail):
        """Test task uses DEFAULT_FROM_EMAIL when from_email is None."""
        from django.conf import settings

        mock_send_mail.return_value = 1

        send_email_async(
            subject="Test",
            message="Body",
            from_email=None,
            recipient_list=["user@example.com"],
        )

        mock_send_mail.assert_called_once()
        call_args = mock_send_mail.call_args
        assert call_args[1]["from_email"] == settings.DEFAULT_FROM_EMAIL

    @patch("core.shared.tasks.email_tasks.send_mail")
    def test_short_subject_is_normalized_before_send(self, mock_send_mail):
        """Provider-invalid short subjects should be expanded before dispatch."""
        mock_send_mail.return_value = 1

        result = send_email_async(
            subject="OTP",
            message="Body",
            from_email="test@ziona.app",
            recipient_list=["user@example.com"],
        )

        assert result["success"] is True
        mock_send_mail.assert_called_once()
        assert mock_send_mail.call_args.kwargs["subject"] == "OTP - Ziona"

    @patch("core.shared.tasks.email_tasks.send_mail")
    def test_empty_subject_uses_default_subject(self, mock_send_mail):
        """Empty subjects should never reach Ensend."""
        mock_send_mail.return_value = 1

        result = send_email_async(
            subject="",
            message="Body",
            from_email="test@ziona.app",
            recipient_list=["user@example.com"],
        )

        assert result["success"] is True
        mock_send_mail.assert_called_once()
        assert mock_send_mail.call_args.kwargs["subject"] == "Ziona Update"

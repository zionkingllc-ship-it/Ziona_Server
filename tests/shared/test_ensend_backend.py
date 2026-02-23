from unittest.mock import MagicMock, patch

import pytest
from django.core.mail import EmailMessage, EmailMultiAlternatives

from core.shared.email_backends.ensend import EnsendEmailBackend


@pytest.fixture
def backend(settings):
    """Create an EnsendEmailBackend with test settings."""
    settings.ENSEND_API_KEY = "test-project-secret"
    settings.ENSEND_API_URL = "https://api.smtpexpress.com/send"
    settings.ENSEND_SENDER_NAME = "Ziona Test"
    settings.DEFAULT_FROM_EMAIL = "test@ziona.app"
    return EnsendEmailBackend(fail_silently=False)


@pytest.fixture
def sample_message():
    """Create a sample EmailMessage."""
    return EmailMessage(
        subject="Test Subject",
        body="Hello, plain text body.",
        from_email="test@ziona.app",
        to=["user@example.com"],
    )


@pytest.fixture
def html_message():
    """Create an EmailMultiAlternatives with HTML content."""
    msg = EmailMultiAlternatives(
        subject="HTML Test",
        body="Plain text fallback.",
        from_email="test@ziona.app",
        to=["user@example.com"],
    )
    msg.attach_alternative("<h1>Hello</h1><p>HTML body</p>", "text/html")
    return msg


class TestSuccessfulSend:
    """Test successful email sending via Ensend API."""

    @patch("core.shared.email_backends.ensend.requests.Session")
    def test_send_single_email(self, mock_session_cls, backend, sample_message):
        """Single email should send and return count of 1."""
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_session.post.return_value = mock_response
        mock_session_cls.return_value = mock_session

        count = backend.send_messages([sample_message])

        assert count == 1
        mock_session.post.assert_called_once()

        call_kwargs = mock_session.post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert payload["subject"] == "Test Subject"
        assert payload["message"] == "Hello, plain text body."
        assert payload["sender"]["email"] == "test@ziona.app"
        assert "user@example.com" in payload["recipients"]["email"]

    @patch("core.shared.email_backends.ensend.requests.Session")
    def test_send_html_email(self, mock_session_cls, backend, html_message):
        """HTML content should be preferred over plain text."""
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_session.post.return_value = mock_response
        mock_session_cls.return_value = mock_session

        count = backend.send_messages([html_message])

        assert count == 1
        call_kwargs = mock_session.post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert "<h1>Hello</h1>" in payload["message"]

    @patch("core.shared.email_backends.ensend.requests.Session")
    def test_send_multiple_emails(self, mock_session_cls, backend):
        """Multiple messages should all be sent."""
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.status_code = 200
        mock_session.post.return_value = mock_response
        mock_session_cls.return_value = mock_session

        messages = [
            EmailMessage(subject=f"Test {i}", body="body", to=[f"user{i}@test.com"])
            for i in range(3)
        ]
        count = backend.send_messages(messages)

        assert count == 3
        assert mock_session.post.call_count == 3


class TestErrorHandling:
    """Test error scenarios and graceful degradation."""

    def test_missing_api_key(self, settings):
        """Should fail loudly when ENSEND_API_KEY is empty."""
        settings.ENSEND_API_KEY = ""
        backend = EnsendEmailBackend(fail_silently=False)
        msg = EmailMessage(subject="Test", body="body", to=["user@test.com"])

        with pytest.raises(ValueError, match="ENSEND_API_KEY"):
            backend.send_messages([msg])

    def test_missing_api_key_silent(self, settings):
        """Should return 0 silently when fail_silently=True."""
        settings.ENSEND_API_KEY = ""
        backend = EnsendEmailBackend(fail_silently=True)
        msg = EmailMessage(subject="Test", body="body", to=["user@test.com"])

        count = backend.send_messages([msg])
        assert count == 0

    @patch("core.shared.email_backends.ensend.requests.Session")
    def test_api_error_4xx(self, mock_session_cls, backend, sample_message):
        """4xx errors should raise unless fail_silently."""
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.ok = False
        mock_response.status_code = 400
        mock_response.text = '{"error": "Bad request"}'
        mock_response.raise_for_status.side_effect = Exception("400 Bad Request")
        mock_session.post.return_value = mock_response
        mock_session_cls.return_value = mock_session

        with pytest.raises(Exception, match="400"):
            backend.send_messages([sample_message])

    @patch("core.shared.email_backends.ensend.requests.Session")
    def test_api_error_5xx(self, mock_session_cls, backend, sample_message):
        """5xx errors should raise unless fail_silently."""
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.ok = False
        mock_response.status_code = 500
        mock_response.text = '{"error": "Internal server error"}'
        mock_response.raise_for_status.side_effect = Exception("500 Server Error")
        mock_session.post.return_value = mock_response
        mock_session_cls.return_value = mock_session

        with pytest.raises(Exception, match="500"):
            backend.send_messages([sample_message])

    @patch("core.shared.email_backends.ensend.requests.Session")
    def test_rate_limit_429(self, mock_session_cls, backend, sample_message):
        """429 rate limit should return False (not sent), not raise."""
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.ok = False
        mock_response.status_code = 429
        mock_response.text = "Rate limited"
        mock_session.post.return_value = mock_response
        mock_session_cls.return_value = mock_session

        count = backend.send_messages([sample_message])
        assert count == 0  # not sent

    @patch("core.shared.email_backends.ensend.requests.Session")
    def test_partial_failure(self, mock_session_cls, backend):
        """If one message fails, others should still send."""
        mock_session = MagicMock()

        success = MagicMock(ok=True, status_code=200)
        failure = MagicMock(ok=False, status_code=429, text="Rate limited")
        mock_session.post.side_effect = [success, failure, success]
        mock_session_cls.return_value = mock_session

        backend_silent = EnsendEmailBackend(fail_silently=True)
        backend_silent.api_key = "test-key"

        messages = [
            EmailMessage(subject=f"Test {i}", body="body", to=[f"u{i}@test.com"])
            for i in range(3)
        ]
        count = backend_silent.send_messages(messages)
        assert count == 2  


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    @patch("core.shared.email_backends.ensend.requests.Session")
    def test_empty_message_list(self, mock_session_cls, backend):
        """Empty list should return 0 without making API calls."""
        count = backend.send_messages([])
        assert count == 0
        mock_session_cls.assert_not_called()

    @patch("core.shared.email_backends.ensend.requests.Session")
    def test_multiple_recipients(self, mock_session_cls, backend):
        """Multiple recipients should be joined with commas."""
        mock_session = MagicMock()
        mock_response = MagicMock(ok=True, status_code=200)
        mock_session.post.return_value = mock_response
        mock_session_cls.return_value = mock_session

        msg = EmailMessage(
            subject="Multi-recipient",
            body="body",
            to=["a@test.com", "b@test.com"],
        )
        backend.send_messages([msg])

        call_kwargs = mock_session.post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert "a@test.com" in payload["recipients"]["email"]
        assert "b@test.com" in payload["recipients"]["email"]

    @patch("core.shared.email_backends.ensend.requests.Session")
    def test_session_reuse(self, mock_session_cls, backend):
        """Session should be created once and reused across messages."""
        mock_session = MagicMock()
        mock_response = MagicMock(ok=True, status_code=200)
        mock_session.post.return_value = mock_response
        mock_session_cls.return_value = mock_session

        messages = [
            EmailMessage(subject=f"Test {i}", body="body", to=[f"u{i}@test.com"])
            for i in range(3)
        ]
        backend.send_messages(messages)

        mock_session_cls.assert_called_once()
        mock_session.close.assert_called_once()

    def test_extract_html_plain_message(self, backend, sample_message):
        """Plain EmailMessage should return None for HTML extraction."""
        html = backend._extract_html(sample_message)
        assert html is None

    def test_extract_html_alternatives(self, backend, html_message):
        """EmailMultiAlternatives should return HTML content."""
        html = backend._extract_html(html_message)
        assert html is not None
        assert "<h1>Hello</h1>" in html

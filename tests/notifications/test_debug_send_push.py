"""debugSendPush diagnostic: per-token FCM outcome, non-destructive, classification.

Covers the service (send_debug_push) and the Firebase boundary (send_fcm_debug).
The GraphQL resolver is a thin @admin_required wrapper over the service, so these
tests exercise the service directly — the established pattern in this codebase.
"""

from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model

from core.notifications.models import DeviceToken
from core.notifications.services import _classify_token, send_debug_push

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email="debugpush@example.com",
        username="debugpush",
        password="password123",  # pragma: allowlist secret
        firebase_uid="firebase-debug-1",
    )


def _token(user, token, platform="ios", is_active=True):
    return DeviceToken.objects.create(
        user=user, token=token, platform=platform, is_active=is_active
    )


def _fake_fcm(tokens, title, body, data):
    """Expo tokens fail (FCM can't deliver), everything else succeeds."""
    out = []
    for t in tokens:
        if t.startswith("ExponentPushToken"):
            out.append(
                {
                    "success": False,
                    "message_id": None,
                    "error_code": "messaging/invalid-argument",
                    "error_message": "not a valid FCM registration token",
                }
            )
        else:
            out.append(
                {"success": True, "message_id": "mid", "error_code": None, "error_message": None}
            )
    return out


def test_classify_token():
    assert _classify_token("ExponentPushToken[joMZfcFiaY3ZwuuWToHj79]") == "expo"
    assert (
        _classify_token("aa72fc19c9a6e56cd104fb1c4f5230db") == "apns_raw"
    )  # pragma: allowlist secret
    assert _classify_token("f" * 64) == "apns_raw"  # raw APNs token length
    assert _classify_token("dEXAMPLE_fcm-token:" + "A" * 150) == "fcm_like"
    assert _classify_token("") == "fcm_like"


@pytest.mark.django_db
@patch("core.notifications.services.get_fcm_project_id", return_value="ziona-app")
@patch("core.notifications.services.send_fcm_debug", side_effect=_fake_fcm)
def test_send_debug_push_reports_per_token_results(mock_send, mock_pid, user):
    _token(user, "ExponentPushToken[joMZfcFiaY3ZwuuWToHj79]", platform="ios")
    _token(user, "d" * 160, platform="android")

    result = send_debug_push(target_user_id=user.id)

    assert result["project_id"] == "ziona-app"
    assert result["tokens_tried"] == 2
    assert result["success_count"] == 1  # the FCM-like token
    assert result["failure_count"] == 1  # the Expo token
    kinds = {r["token_kind"] for r in result["results"]}
    assert kinds == {"expo", "fcm_like"}
    # Tokens are always masked, never returned in full.
    assert all(
        len(r["token_preview"]) <= 20 or "…" in r["token_preview"] for r in result["results"]
    )
    # It sent exactly the registered tokens.
    assert len(mock_send.call_args.args[0]) == 2


@pytest.mark.django_db
@patch("core.notifications.services.get_fcm_project_id", return_value="ziona-app")
@patch("core.notifications.services.send_fcm_debug", side_effect=_fake_fcm)
def test_send_debug_push_is_non_destructive(mock_send, mock_pid, user):
    _token(user, "ExponentPushToken[willFail]", platform="ios")

    send_debug_push(target_user_id=user.id)

    # Even though the Expo token "failed", debug must NOT deactivate it.
    assert DeviceToken.objects.filter(user=user, is_active=True).count() == 1


@pytest.mark.django_db
@patch("core.notifications.services.get_fcm_project_id", return_value="ziona-app")
@patch("core.notifications.services.send_fcm_debug", side_effect=_fake_fcm)
def test_send_debug_push_excludes_inactive_by_default(mock_send, mock_pid, user):
    _token(user, "a" * 160, platform="android", is_active=True)
    _token(user, "b" * 160, platform="android", is_active=False)

    assert send_debug_push(target_user_id=user.id)["tokens_tried"] == 1
    assert send_debug_push(target_user_id=user.id, include_inactive=True)["tokens_tried"] == 2


@pytest.mark.django_db
@patch("core.notifications.services.get_fcm_project_id", return_value="ziona-app")
@patch("core.notifications.services.send_fcm_debug", return_value=[])
def test_send_debug_push_no_tokens(mock_send, mock_pid, user):
    result = send_debug_push(target_user_id=user.id)
    assert result["tokens_tried"] == 0
    assert result["success_count"] == 0
    assert result["results"] == []


def test_send_fcm_debug_empty_tokens_returns_empty():
    from core.notifications.firebase import send_fcm_debug

    assert send_fcm_debug([], "t", "b", {}) == []


def test_send_fcm_debug_maps_success_and_failure(monkeypatch):
    """The Firebase boundary maps BatchResponse → per-token dicts, no side effects."""
    import core.notifications.firebase as fb

    monkeypatch.setattr(fb, "initialize_firebase", lambda: None)
    monkeypatch.setattr(fb, "_firebase_initialized", True)
    monkeypatch.setattr(fb, "firebase_admin", MagicMock())  # not None

    class _Resp:
        def __init__(self, success, message_id=None, exception=None):
            self.success = success
            self.message_id = message_id
            self.exception = exception

    class _Batch:
        responses = [
            _Resp(True, message_id="mid-1"),
            _Resp(False, exception=type("E", (), {"code": "messaging/invalid-argument"})()),
        ]

    fake_messaging = MagicMock()
    fake_messaging.send_each_for_multicast.return_value = _Batch()
    monkeypatch.setattr(fb, "messaging", fake_messaging)

    out = fb.send_fcm_debug(["tokA", "tokB"], "t", "b", {"k": "v"})

    assert out[0] == {
        "success": True,
        "message_id": "mid-1",
        "error_code": None,
        "error_message": None,
    }
    assert out[1]["success"] is False
    assert out[1]["error_code"] == "messaging/invalid-argument"

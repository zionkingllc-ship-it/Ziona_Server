"""Offline FCM boundary regression checks; no credentials, database, or sends."""
import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch


class UnregisteredError(Exception):
    code = "NOT_FOUND"

class SenderIdMismatchError(Exception):
    code = "PERMISSION_DENIED"

class PayloadError(Exception):
    code = "INVALID_ARGUMENT"

class FirebaseBoundaryTests(unittest.TestCase):
    def setUp(self):
        django = ModuleType("django")
        conf = ModuleType("django.conf")
        conf.settings = SimpleNamespace()
        admin = ModuleType("firebase_admin")
        admin.credentials = MagicMock()
        admin.messaging = MagicMock()
        admin.messaging.UnregisteredError = UnregisteredError
        admin.messaging.SenderIdMismatchError = SenderIdMismatchError
        admin.get_app = MagicMock(return_value=SimpleNamespace(project_id="test-project"))
        models = ModuleType("core.notifications.models")
        models.DeviceToken = MagicMock()
        with patch.dict(sys.modules, {"django": django, "django.conf": conf,
                                     "firebase_admin": admin, "core.notifications.models": models}):
            spec = importlib.util.spec_from_file_location("fcm_under_test", Path(__file__).resolve().parents[1] / "core/notifications/firebase.py")
            self.fb = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.fb)
        self.fb._firebase_initialized = True
        self.messaging = admin.messaging
        self.tokens = models.DeviceToken

    def send_error(self, error):
        self.messaging.send_each_for_multicast.return_value = SimpleNamespace(
            success_count=0, failure_count=1,
            responses=[SimpleNamespace(success=False, exception=error)])
        return self.fb.send_fcm_notification(["fake-token"], "Title", "Body", {})

    def test_payload_error_preserves_token(self):
        summary = self.send_error(PayloadError("bad payload"))
        self.tokens.objects.filter.assert_not_called()
        self.assertEqual(summary["failure_count"], 1)

    def test_project_mismatch_preserves_token_and_logs(self):
        with self.assertLogs(self.fb.logger, level="ERROR") as logs:
            self.send_error(SenderIdMismatchError("wrong project"))
        self.tokens.objects.filter.assert_not_called()
        self.assertTrue(any("fcm_sender_id_mismatch" in x for x in logs.output))

    def test_unregistered_token_is_deactivated(self):
        summary = self.send_error(UnregisteredError("uninstalled"))
        self.tokens.objects.filter.return_value.update.assert_called_once_with(is_active=False)
        self.assertEqual(summary["invalid_token_count"], 1)

    def test_transport_failure_is_counted(self):
        self.messaging.send_each_for_multicast.side_effect = RuntimeError("offline")
        summary = self.fb.send_fcm_notification(["one", "two"], "Title", "Body", {})
        self.assertEqual(summary["failure_count"], 2)

    def test_missing_sdk_is_counted(self):
        self.fb.firebase_admin = None
        summary = self.fb.send_fcm_notification(["one"], "Title", "Body", {})
        self.assertEqual(summary["failure_count"], 1)

    def test_ios_alert_and_android_channel(self):
        self.fb._build_multicast_message(["one"], "Title", "Body", {"destinationRoute": "/viewer/post"})
        self.messaging.ApsAlert.assert_called_once_with(title="Title", body="Body")
        self.assertEqual(self.messaging.APNSConfig.call_args.kwargs["headers"]["apns-push-type"], "alert")
        self.assertEqual(self.messaging.AndroidNotification.call_args.kwargs["channel_id"], "default")

if __name__ == "__main__":
    unittest.main()

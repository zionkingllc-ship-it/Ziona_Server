from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from core.media.management.commands.configure_gcs_cors import (
    METHODS,
    RESPONSE_HEADERS,
)


class _Bucket:
    def __init__(self, cors):
        self.cors = cors
        self.reloaded = False

    def reload(self):
        self.reloaded = True


class _StorageClient:
    def __init__(self, bucket):
        self._bucket = bucket

    def bucket(self, _name):
        return self._bucket


def _policy(origins):
    return [
        {
            "origin": origins,
            "method": METHODS,
            "responseHeader": RESPONSE_HEADERS,
            "maxAgeSeconds": 3600,
        }
    ]


def test_gcs_cors_check_succeeds_when_live_policy_matches(settings, monkeypatch):
    settings.GCP_STORAGE_BUCKET = "ziona-media-dev"
    settings.GCS_CORS_ALLOWED_ORIGINS = ["https://staging.example.com"]
    bucket = _Bucket(_policy(["https://staging.example.com"]))
    monkeypatch.setattr(
        "core.media.management.commands.configure_gcs_cors._build_storage_client",
        lambda: _StorageClient(bucket),
    )
    output = StringIO()

    call_command("configure_gcs_cors", "--check", stdout=output)

    assert bucket.reloaded is True
    assert "GCS CORS policy matches" in output.getvalue()


def test_gcs_cors_check_fails_on_policy_drift(settings, monkeypatch):
    settings.GCP_STORAGE_BUCKET = "ziona-media-prod"
    settings.GCS_CORS_ALLOWED_ORIGINS = ["https://admin.ziona.app"]
    bucket = _Bucket(_policy(["https://wrong.example.com"]))
    monkeypatch.setattr(
        "core.media.management.commands.configure_gcs_cors._build_storage_client",
        lambda: _StorageClient(bucket),
    )

    with pytest.raises(CommandError, match="policy drift"):
        call_command("configure_gcs_cors", "--check")

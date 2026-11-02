"""The configured Google audience list must control both deployed environments."""

import json
import os
import subprocess
import sys
from pathlib import Path


def _load_client_ids(settings_module: str, configured_ids: str | None) -> list[str]:
    env = os.environ.copy()
    for key in (
        "GOOGLE_CLIENT_IDS",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_OAUTH_CLIENT_ID",
        "GOOGLE_IOS_CLIENT_ID",
        "GOOGLE_ANDROID_CLIENT_ID",
    ):
        env.pop(key, None)
    env.update(
        {
            "DJANGO_SETTINGS_MODULE": settings_module,
            "DJANGO_SECRET_KEY": "settings-test-django-secret",  # pragma: allowlist secret
            "JWT_SECRET_KEY": "settings-test-jwt-secret",  # pragma: allowlist secret
            "ENCRYPTION_KEY": "settings-test-encryption-key",  # pragma: allowlist secret
            "GCP_STORAGE_BUCKET": "ziona-media-settings-test",
            "GCP_CREDENTIALS_FILE": "settings-test-gcp.json",
            "FIREBASE_CREDENTIALS_FILE": "settings-test-firebase.json",
            "GOOGLE_CLIENT_ID": "legacy-client.apps.googleusercontent.com",
            "DATABASE_URL": "sqlite:///db.sqlite3",
            "REDIS_URL": "redis://localhost:6379/0",
        }
    )
    if configured_ids is not None:
        env["GOOGLE_CLIENT_IDS"] = configured_ids
    result = subprocess.run(
        [  # noqa: S603 - fixed interpreter/arguments; no shell or user input
            sys.executable,
            "-c",
            "import json; from django.conf import settings; "
            "print(json.dumps(settings.GOOGLE_CLIENT_IDS))",
        ],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_staging_accepts_only_explicitly_configured_audiences():
    configured = (
        " staging-web.apps.googleusercontent.com,staging-ios.apps.googleusercontent.com,"
        " staging-web.apps.googleusercontent.com "
    )
    assert _load_client_ids("config.settings.staging", configured) == [
        "staging-web.apps.googleusercontent.com",
        "staging-ios.apps.googleusercontent.com",
    ]


def test_staging_removed_audience_is_not_readded_by_code():
    assert _load_client_ids(
        "config.settings.staging", "replacement-client.apps.googleusercontent.com"
    ) == ["replacement-client.apps.googleusercontent.com"]


def test_production_accepts_only_explicitly_configured_audiences():
    assert _load_client_ids(
        "config.settings.production", "production-client.apps.googleusercontent.com"
    ) == ["production-client.apps.googleusercontent.com"]


def test_unset_allowlist_falls_back_to_legacy_client_id():
    assert _load_client_ids("config.settings.staging", None) == [
        "legacy-client.apps.googleusercontent.com"
    ]


def test_empty_explicit_allowlist_does_not_fall_back_to_legacy_client_id():
    assert _load_client_ids("config.settings.staging", "") == []

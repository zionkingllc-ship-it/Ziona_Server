"""Staging must accept the staging/dev mobile Google audiences without leaking into production."""

import json
import os
import subprocess
import sys
from pathlib import Path

STAGING_MOBILE_CLIENT_IDS = {
    "273573551303-lhghjjbh4qa1tad0ml3bat95igggfp18.apps.googleusercontent.com",
    "273573551303-fpipdq89n28lv7t3la0bsh1n5do0bp89.apps.googleusercontent.com",
    "273573551303-bafar3va69072q29jbcgp2l4aivs7mol.apps.googleusercontent.com",
}


def _load_client_ids(settings_module: str, configured_ids: str) -> list[str]:
    env = os.environ.copy()
    env.update(
        {
            "DJANGO_SETTINGS_MODULE": settings_module,
            "DJANGO_SECRET_KEY": "settings-test-django-secret",  # pragma: allowlist secret
            "JWT_SECRET_KEY": "settings-test-jwt-secret",  # pragma: allowlist secret
            "ENCRYPTION_KEY": "settings-test-encryption-key",  # pragma: allowlist secret
            "GCP_STORAGE_BUCKET": "ziona-media-settings-test",
            "GCP_CREDENTIALS_FILE": "settings-test-gcp.json",
            "FIREBASE_CREDENTIALS_FILE": "settings-test-firebase.json",
            "GOOGLE_CLIENT_IDS": configured_ids,
            "DATABASE_URL": "sqlite:///db.sqlite3",
            "REDIS_URL": "redis://localhost:6379/0",
        }
    )
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


def test_staging_unions_dev_mobile_audiences_with_configured_ids():
    legacy_id = "legacy-staging-client.apps.googleusercontent.com"

    client_ids = _load_client_ids("config.settings.staging", legacy_id)

    assert legacy_id in client_ids
    assert set(client_ids) >= STAGING_MOBILE_CLIENT_IDS


def test_production_does_not_inherit_staging_mobile_audiences():
    production_id = "production-client.apps.googleusercontent.com"

    client_ids = _load_client_ids("config.settings.production", production_id)

    assert client_ids == [production_id]

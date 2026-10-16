"""The configured Android signing allowlist must never be silently expanded."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "package",
    ["com.zionking.ziona", "com.zionking.ziona.staging"],
)
def test_explicit_android_fingerprints_override_release_defaults(package):
    """An environment can add or revoke a certificate without a code change."""
    env = os.environ.copy()
    env.update(
        {
            "DJANGO_SETTINGS_MODULE": "config.settings.dev",
            "ANDROID_APP_PACKAGE_NAME": package,
            "ANDROID_SHA256_CERT_FINGERPRINTS": "AA:BB:CC,DD:EE:FF",
        }
    )
    result = subprocess.run(
        [  # noqa: S603 - fixed interpreter/arguments; no shell or user input
            sys.executable,
            "-c",
            "import json; from django.conf import settings; "
            "print(json.dumps(settings.ANDROID_SHA256_CERT_FINGERPRINTS))",
        ],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    assert json.loads(result.stdout) == ["AA:BB:CC", "DD:EE:FF"]

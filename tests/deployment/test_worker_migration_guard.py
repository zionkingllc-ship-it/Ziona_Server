"""Workers must not run new code on the old schema during a deploy.

Render migrates only in the web service's pre-deploy step and deploys the
worker alongside it, so each worker's start command waits for that migration.
"""

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


def test_workers_wait_for_the_web_deploy_to_migrate(settings):
    blueprint = (settings.BASE_DIR / "render.yaml").read_text(encoding="utf-8")

    for worker in ("ziona-worker-staging", "ziona-worker-prod"):
        block = blueprint.split(f"name: {worker}", 1)[1].split("\n  - type:", 1)[0]
        assert "startCommand: python manage.py wait_for_migrations --timeout 600 && " in block


def test_validator_rejects_a_worker_that_does_not_wait_for_migrations(settings, tmp_path):
    blueprint = (settings.BASE_DIR / "render.yaml").read_text(encoding="utf-8")
    (tmp_path / "render.yaml").write_text(
        blueprint.replace("python manage.py wait_for_migrations --timeout 600 && ", "", 1),
        encoding="utf-8",
    )
    settings.BASE_DIR = tmp_path

    with pytest.raises(CommandError, match="ziona-worker-staging"):
        call_command("validate_render_blueprint", stdout=StringIO())

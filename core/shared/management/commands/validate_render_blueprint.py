"""Project-specific checks for the Render blueprint."""

import re
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

REQUIRED_SERVICES = {
    "ziona-api-staging",
    "ziona-worker-staging",
    "ziona-api-prod",
    "ziona-worker-prod",
    "ziona-cron-check-scheduled-anchors",
    "ziona-cron-refresh-dashboard-cache",
    "ziona-cron-refresh-company-stats",
    "ziona-cron-daily-anchor-notifications",
    "ziona-cron-notification-digest",
    "ziona-cron-daily-analytics",
    "ziona-cron-expired-anchor-purge",
    "ziona-cron-notification-cleanup",
    "ziona-cron-stale-media-cleanup",
}

REQUIRED_ENV_NAMES = {
    "DJANGO_SETTINGS_MODULE",
    "ALLOWED_HOSTS",
    "CORS_ALLOWED_ORIGINS",
    "GCS_CORS_ALLOWED_ORIGINS",
    "GCP_STORAGE_BUCKET",
    "GCP_CREDENTIALS_FILE",
    "FIREBASE_CREDENTIALS_FILE",
    "DATABASE_URL",
    "REDIS_URL",
    "CELERY_BROKER_URL",
    "CELERY_RESULT_BACKEND",
    "DJANGO_SECRET_KEY",
    "JWT_SECRET_KEY",
    "ENCRYPTION_KEY",
}


class Command(BaseCommand):
    """Validate the fields we rely on before deploy."""

    help = "Validate Ziona's Render blueprint contains required prod services and env names."

    def handle(self, *args, **options):
        blueprint_path = Path(settings.BASE_DIR) / "render.yaml"
        text = blueprint_path.read_text(encoding="utf-8")

        missing_services = sorted(
            service for service in REQUIRED_SERVICES if f"name: {service}" not in text
        )
        if missing_services:
            raise CommandError(f"render.yaml missing services: {', '.join(missing_services)}")

        missing_env = sorted(
            env_name for env_name in REQUIRED_ENV_NAMES if f"key: {env_name}" not in text
        )
        if missing_env:
            raise CommandError(f"render.yaml missing env vars: {', '.join(missing_env)}")

        missing_checks_pass = []
        for service in sorted(REQUIRED_SERVICES):
            service_match = re.search(
                rf"(?ms)^  - type: [^\n]+\n    name: {re.escape(service)}\n.*?(?=^  - type: |\Z)",
                text,
            )
            if service_match and "autoDeployTrigger: checksPass" not in service_match.group(0):
                missing_checks_pass.append(service)

        if missing_checks_pass:
            raise CommandError(
                "render.yaml services must deploy from Git after checks pass: "
                + ", ".join(missing_checks_pass)
            )

        if "ziona-beat-prod" in text:
            raise CommandError("render.yaml must use Render cron jobs, not ziona-beat-prod")

        if any(
            command in text
            for command in ("setup_test_admin", "seed_circle_sample_data", "import_bible")
        ):
            raise CommandError("render.yaml still contains staging/test seed commands")

        self.stdout.write(self.style.SUCCESS("render.yaml production blueprint checks passed"))

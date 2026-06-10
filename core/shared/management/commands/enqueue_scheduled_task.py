"""Enqueue known scheduled Celery tasks from Render cron jobs."""

from importlib import import_module

from django.core.management.base import BaseCommand, CommandError

SCHEDULED_TASKS = {
    "send-daily-anchor-notifications": (
        "core.notifications.tasks",
        "send_daily_anchor_notifications",
    ),
    "cleanup-old-notifications": (
        "core.notifications.tasks",
        "cleanup_old_notifications",
    ),
    "send-daily-notification-digest": (
        "core.notifications.tasks",
        "send_daily_notification_digest",
    ),
    "calculate-daily-analytics": (
        "core.admin_dashboard.tasks",
        "calculate_daily_analytics",
    ),
    "refresh-dashboard-cache": (
        "core.admin_dashboard.tasks",
        "refresh_dashboard_cache",
    ),
    "check-scheduled-anchors": (
        "core.admin_dashboard.tasks",
        "check_scheduled_anchors",
    ),
    "refresh-company-stats": (
        "core.landing.tasks",
        "refresh_company_stats",
    ),
    "purge-expired-anchors": (
        "core.circles.tasks",
        "purge_expired_anchors",
    ),
    "cleanup-stale-media": (
        "core.media.tasks",
        "cleanup_stale_media_uploads",
    ),
}


class Command(BaseCommand):
    """Queue one allowlisted scheduled task and exit quickly."""

    help = "Enqueue a known scheduled Celery task for Render cron jobs."

    def add_arguments(self, parser):
        parser.add_argument("task_name", choices=sorted(SCHEDULED_TASKS))
        parser.add_argument(
            "--sync",
            action="store_true",
            help="Run the task inline instead of enqueueing it. Intended for local smoke tests.",
        )

    def handle(self, *args, **options):
        module_name, attr_name = SCHEDULED_TASKS[options["task_name"]]
        try:
            task = getattr(import_module(module_name), attr_name)
        except Exception as exc:  # noqa: BLE001
            raise CommandError(
                f"Could not load scheduled task {options['task_name']}: {exc}"
            ) from exc

        if options["sync"]:
            result = task.apply()
            if result.failed():
                raise CommandError(f"Scheduled task failed: {result.result}")
            self.stdout.write(self.style.SUCCESS(f"Ran {options['task_name']} inline"))
            return

        async_result = task.delay()
        self.stdout.write(
            self.style.SUCCESS(f"Queued {options['task_name']} as task {async_result.id}")
        )

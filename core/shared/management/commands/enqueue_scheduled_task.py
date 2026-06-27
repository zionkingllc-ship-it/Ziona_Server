"""Enqueue known scheduled Celery tasks from Render cron jobs."""

from importlib import import_module

from django.conf import settings
from django.core.cache import cache
from django.core.management.base import BaseCommand, CommandError

SCHEDULED_TASKS = {
    "send-daily-anchor-notifications": {
        "module": "core.notifications.tasks",
        "attr": "send_daily_anchor_notifications",
        "lock_timeout": 30 * 60,
    },
    "cleanup-old-notifications": {
        "module": "core.notifications.tasks",
        "attr": "cleanup_old_notifications",
        "lock_timeout": 2 * 60 * 60,
    },
    "send-daily-notification-digest": {
        "module": "core.notifications.tasks",
        "attr": "send_daily_notification_digest",
        "lock_timeout": 2 * 60 * 60,
    },
    "calculate-daily-analytics": {
        "module": "core.admin_dashboard.tasks",
        "attr": "calculate_daily_analytics",
        "lock_timeout": 3 * 60 * 60,
    },
    "refresh-dashboard-cache": {
        "module": "core.admin_dashboard.tasks",
        "attr": "refresh_dashboard_cache",
        "lock_timeout": 20 * 60,
    },
    "check-scheduled-anchors": {
        "module": "core.admin_dashboard.tasks",
        "attr": "check_scheduled_anchors",
        "lock_timeout": 10 * 60,
    },
    "refresh-company-stats": {
        "module": "core.landing.tasks",
        "attr": "refresh_company_stats",
        "lock_timeout": 90 * 60,
    },
    "purge-expired-anchors": {
        "module": "core.circles.tasks",
        "attr": "purge_expired_anchors",
        "lock_timeout": 2 * 60 * 60,
    },
    "cleanup-stale-media": {
        "module": "core.media.tasks",
        "attr": "cleanup_stale_media_uploads",
        "lock_timeout": 10 * 60,
    },
    "purge-due-account-deletions": {
        "module": "core.users.tasks",
        "attr": "purge_due_account_deletions",
        "lock_timeout": 55 * 60,
    },
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
        parser.add_argument(
            "--force",
            action="store_true",
            help="Bypass the single-flight cron lock and enqueue anyway.",
        )

    def handle(self, *args, **options):
        task_config = SCHEDULED_TASKS[options["task_name"]]
        module_name = task_config["module"]
        attr_name = task_config["attr"]
        lock_key = build_scheduled_task_lock_key(options["task_name"])
        lock_timeout = task_config["lock_timeout"]
        try:
            task = getattr(import_module(module_name), attr_name)
        except Exception as exc:  # noqa: BLE001
            raise CommandError(
                f"Could not load scheduled task {options['task_name']}: {exc}"
            ) from exc

        lock_acquired = options["force"] or cache.add(lock_key, "1", lock_timeout)
        if not lock_acquired:
            self.stdout.write(
                self.style.WARNING(
                    f"Skipped {options['task_name']}: an existing scheduled run is still in flight."
                )
            )
            return

        try:
            if options["sync"]:
                result = task.apply()
                if result.failed():
                    raise CommandError(f"Scheduled task failed: {result.result}")
                self.stdout.write(self.style.SUCCESS(f"Ran {options['task_name']} inline"))
                return

            async_result = task.apply_async(
                queue=settings.CELERY_QUEUE_CRON,
                priority=settings.CELERY_CRON_TASK_PRIORITY,
                headers={"scheduled_task_name": options["task_name"]},
            )
        except Exception:
            cache.delete(lock_key)
            raise

        self.stdout.write(
            self.style.SUCCESS(
                f"Queued {options['task_name']} as task {async_result.id} "
                f"on queue {settings.CELERY_QUEUE_CRON}"
            )
        )


def build_scheduled_task_lock_key(task_name: str) -> str:
    """Return the cache key used to prevent duplicate cron enqueue bursts."""
    return f"scheduled-task-lock:{task_name}"

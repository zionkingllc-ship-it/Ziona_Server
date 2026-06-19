from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.management import call_command

from core.shared.management.commands.enqueue_scheduled_task import (
    build_scheduled_task_lock_key,
)


def test_enqueue_scheduled_task_uses_cron_queue_and_priority(settings):
    settings.CELERY_QUEUE_CRON = "cron"
    settings.CELERY_CRON_TASK_PRIORITY = 1
    fake_task = MagicMock()
    fake_task.apply_async.return_value = SimpleNamespace(id="cron-task-123")

    with (
        patch(
            "core.shared.management.commands.enqueue_scheduled_task.import_module",
            return_value=SimpleNamespace(check_scheduled_anchors=fake_task),
        ),
        patch(
            "core.shared.management.commands.enqueue_scheduled_task.cache.add", return_value=True
        ),
    ):
        output = StringIO()
        call_command("enqueue_scheduled_task", "check-scheduled-anchors", stdout=output)

    fake_task.apply_async.assert_called_once_with(
        queue="cron",
        priority=1,
        headers={"scheduled_task_name": "check-scheduled-anchors"},
    )
    assert "Queued check-scheduled-anchors as task cron-task-123 on queue cron" in output.getvalue()


def test_enqueue_scheduled_task_skips_when_single_flight_lock_exists():
    fake_task = MagicMock()

    with (
        patch(
            "core.shared.management.commands.enqueue_scheduled_task.import_module",
            return_value=SimpleNamespace(check_scheduled_anchors=fake_task),
        ),
        patch(
            "core.shared.management.commands.enqueue_scheduled_task.cache.add", return_value=False
        ),
    ):
        output = StringIO()
        call_command("enqueue_scheduled_task", "check-scheduled-anchors", stdout=output)

    fake_task.apply_async.assert_not_called()
    assert "still in flight" in output.getvalue()


def test_build_scheduled_task_lock_key_is_stable():
    assert (
        build_scheduled_task_lock_key("refresh-dashboard-cache")
        == "scheduled-task-lock:refresh-dashboard-cache"
    )

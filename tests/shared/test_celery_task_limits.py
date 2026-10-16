"""Every Celery task's soft time limit must be able to fire before its hard limit.

The soft limit raises SoftTimeLimitExceeded inside the task, so cleanup and
retry logic run. The hard limit kills the worker process outright. A soft limit
at or above the hard one can never fire — send_daily_notification_digest
declared 600s soft while inheriting the 300s global hard limit, so it was
always killed at 300s with no chance to stop cleanly.
"""

from importlib import import_module

import pytest
from django.conf import settings

from config.celery import app
from core.shared.management.commands.enqueue_scheduled_task import SCHEDULED_TASKS


def _effective_limits(task) -> tuple[float, float]:
    soft = task.soft_time_limit
    hard = task.time_limit
    return (
        settings.CELERY_TASK_SOFT_TIME_LIMIT if soft is None else soft,
        settings.CELERY_TASK_TIME_LIMIT if hard is None else hard,
    )


def _project_tasks():
    app.loader.import_default_modules()
    return sorted(
        (name, task) for name, task in app.tasks.items() if not name.startswith("celery.")
    )


def test_global_defaults_leave_room_for_the_soft_limit():
    assert settings.CELERY_TASK_SOFT_TIME_LIMIT < settings.CELERY_TASK_TIME_LIMIT


@pytest.mark.parametrize(("name", "task"), _project_tasks(), ids=lambda value: str(value))
def test_soft_limit_fires_before_the_hard_limit(name, task):
    soft, hard = _effective_limits(task)
    assert soft < hard, f"{name}: soft {soft}s can never fire before hard {hard}s"


@pytest.mark.parametrize("schedule_name", sorted(SCHEDULED_TASKS))
def test_scheduled_tasks_declare_their_own_limits(schedule_name):
    """Scheduled tasks sweep tables that grow, so their budget is a decision, not a default."""
    spec = SCHEDULED_TASKS[schedule_name]
    task = getattr(import_module(spec["module"]), spec["attr"])

    assert task.soft_time_limit is not None, f"{schedule_name} has no soft_time_limit"
    assert task.time_limit is not None, f"{schedule_name} has no time_limit"

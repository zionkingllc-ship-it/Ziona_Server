from io import StringIO

from django.core.management import call_command

from core.shared.management.commands.enqueue_scheduled_task import SCHEDULED_TASKS


def test_render_blueprint_matches_low_cost_prod_topology(settings):
    output = StringIO()

    call_command("validate_render_blueprint", stdout=output)

    text = output.getvalue()
    assert "production blueprint checks passed" in text


def test_render_blueprint_uses_cron_jobs_not_prod_beat(settings):
    blueprint = (settings.BASE_DIR / "render.yaml").read_text(encoding="utf-8")

    assert "name: ziona-worker-prod" in blueprint
    assert "name: ziona-beat-prod" not in blueprint
    assert "type: cron" in blueprint
    assert "python manage.py enqueue_scheduled_task" in blueprint
    assert "-Q email,default,media,cron" in blueprint
    assert 'schedule: "*/5 * * * *"' in blueprint
    assert 'schedule: "*/15 * * * *"' in blueprint


def test_render_cron_task_allowlist_covers_expected_schedules():
    expected = {
        "send-daily-anchor-notifications",
        "cleanup-old-notifications",
        "send-daily-notification-digest",
        "calculate-daily-analytics",
        "refresh-dashboard-cache",
        "check-scheduled-anchors",
        "refresh-company-stats",
        "purge-expired-anchors",
        "cleanup-stale-media",
        "purge-due-account-deletions",
    }

    assert expected.issubset(SCHEDULED_TASKS)

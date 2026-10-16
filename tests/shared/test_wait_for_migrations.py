"""wait_for_migrations — holds the Celery worker back until the web deploy has migrated."""

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import OperationalError, connection
from django.db.migrations.recorder import MigrationRecorder

from core.shared.management.commands import wait_for_migrations
from core.shared.management.commands.wait_for_migrations import Command


def _run(**options) -> str:
    out = StringIO()
    call_command("wait_for_migrations", stdout=out, stderr=StringIO(), **options)
    return out.getvalue()


@pytest.mark.django_db
def test_returns_at_once_when_the_schema_is_current():
    assert "All migrations are applied" in _run(timeout=0)


@pytest.mark.django_db
def test_sees_a_real_unapplied_migration():
    """Exercises the real migration graph against the database, no mocks."""
    MigrationRecorder(connection).record_unapplied("users", "0013_user_daily_activity")

    assert Command().pending_migrations("default") == 1
    with pytest.raises(CommandError, match="1 migration\\(s\\) pending"):
        _run(timeout=0)


def test_waits_until_the_web_deploy_has_migrated(monkeypatch):
    results = iter([2, None, 0])
    monkeypatch.setattr(Command, "pending_migrations", lambda self, database: next(results))

    output = _run(timeout=60, interval=0)

    assert "2 migration(s) pending" in output
    assert "database unreachable" in output
    assert "All migrations are applied" in output


def test_gives_up_with_a_failing_exit_after_the_timeout(monkeypatch):
    monkeypatch.setattr(Command, "pending_migrations", lambda self, database: 3)

    with pytest.raises(CommandError, match="Gave up after 0s: 3 migration\\(s\\) pending"):
        _run(timeout=0, interval=0)


def test_an_unreachable_database_is_waited_on_not_raised(monkeypatch):
    def unreachable(_connection):
        raise OperationalError("could not connect to server")

    monkeypatch.setattr(wait_for_migrations, "MigrationExecutor", unreachable)

    assert Command(stderr=StringIO()).pending_migrations("default") is None

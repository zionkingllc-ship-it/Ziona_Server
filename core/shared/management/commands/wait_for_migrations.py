"""Block until the database schema has every migration this code expects.

Render runs ``migrate`` only as the web service's pre-deploy step, and deploys
the Celery worker alongside it, not after it. The worker's build is the shorter
one, so new worker code could start consuming tasks on the old schema and fail
on columns that do not exist yet. Prefixed to the worker's start command, this
holds the worker back until the web deploy has migrated; tasks queued in the
meantime wait in the broker instead of failing.

Deliberately not ``migrate`` itself: two services applying migrations at once
race each other. One owner migrates; everyone else waits for it.
"""

import time

from django.core.management.base import BaseCommand, CommandError
from django.db import DEFAULT_DB_ALIAS, connections
from django.db.migrations.executor import MigrationExecutor
from django.db.utils import DatabaseError


class Command(BaseCommand):
    help = "Wait until no migrations are pending, then exit 0 (exit 1 on timeout)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--timeout",
            type=float,
            default=600,
            help="Seconds to wait before giving up (default: 600).",
        )
        parser.add_argument(
            "--interval",
            type=float,
            default=5,
            help="Seconds between checks (default: 5).",
        )
        parser.add_argument("--database", default=DEFAULT_DB_ALIAS)

    def handle(self, *args, timeout, interval, database, **options):
        deadline = time.monotonic() + timeout

        while True:
            pending = self.pending_migrations(database)
            if pending == 0:
                self.stdout.write(self.style.SUCCESS("All migrations are applied."))
                return

            status = (
                "database unreachable" if pending is None else f"{pending} migration(s) pending"
            )
            if time.monotonic() >= deadline:
                raise CommandError(f"Gave up after {timeout:g}s: {status}.")

            self.stdout.write(f"Waiting for migrations: {status}.")
            time.sleep(interval)

    def pending_migrations(self, database: str) -> int | None:
        """Count migrations still to apply, or None while the database is unreachable.

        Migrations recorded in the database but absent from this code — the
        state after rolling a deploy back — are not pending, so a rollback is
        never held up here.
        """
        connection = connections[database]
        try:
            executor = MigrationExecutor(connection)
            return len(executor.migration_plan(executor.loader.graph.leaf_nodes()))
        except DatabaseError as exc:
            self.stderr.write(f"Database not ready: {exc}")
            # Drop a broken connection so the next check reconnects.
            connection.close_if_unusable_or_obsolete()
            return None

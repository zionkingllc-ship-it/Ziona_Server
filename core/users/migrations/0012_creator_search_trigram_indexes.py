"""Trigram GIN indexes so Discover creator search stays fast at scale.

The DDL lives in core/users/search_indexes.py so the same logic backs the
`create_search_indexes` management command — see that module for why the index
expression uses upper() and why the pg_trgm schema is detected.

This migration deliberately **skips rather than fails** when pg_trgm is not
available: migrations run during the Render build, so a missing extension must
not break a release. Once pg_trgm is enabled, run:

    python manage.py create_search_indexes

No-ops entirely on non-PostgreSQL (the SQLite test database).
"""

from django.db import migrations

from core.users.search_indexes import create_search_indexes, drop_search_indexes


def forwards(apps, schema_editor):
    create_search_indexes(schema_editor.connection)


def backwards(apps, schema_editor):
    drop_search_indexes(schema_editor.connection)


class Migration(migrations.Migration):
    # CREATE INDEX CONCURRENTLY cannot run inside a transaction.
    atomic = False

    dependencies = [
        ("users", "0011_account_lifecycle_and_deletion_request"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]

"""Trigram search index helpers must never break a non-PostgreSQL run.

Migrations execute during the Render build, so a missing pg_trgm extension must
skip rather than fail the release. CI runs on SQLite, which has no GIN/trgm at
all — these lock in that both paths degrade quietly.
"""

from django.db import connection

from core.users.search_indexes import (
    SEARCH_INDEXES,
    create_search_indexes,
    drop_search_indexes,
    trigram_schema,
)


def test_index_expressions_match_djangos_icontains_sql():
    """Django compiles icontains to UPPER(col::text) on PostgreSQL.

    An index on lower(col) would be silently ignored by the planner, so this
    guards the one detail that makes the index actually get used.
    """
    expressions = {expression for _, expression in SEARCH_INDEXES}

    assert expressions == {"upper(username::text)", "upper(full_name::text)"}
    assert all("lower(" not in expression for expression in expressions)


def test_create_is_a_no_op_on_non_postgres():
    """On SQLite (CI) this must return False, not raise."""
    if connection.vendor == "postgresql":
        return  # covered by the real database path

    assert create_search_indexes(connection) is False
    drop_search_indexes(connection)  # must not raise either


def test_trigram_schema_is_detected_not_hardcoded(monkeypatch):
    """Supabase installs pg_trgm in `extensions`; vanilla PG uses `public`."""

    class FakeCursor:
        def __init__(self, row):
            self._row = row

        def execute(self, *args, **kwargs):
            return None

        def fetchone(self):
            return self._row

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class FakeConnection:
        vendor = "postgresql"

        def __init__(self, row):
            self._row = row

        def cursor(self):
            return FakeCursor(self._row)

    assert trigram_schema(FakeConnection(("extensions",))) == "extensions"
    assert trigram_schema(FakeConnection(("public",))) == "public"
    # Extension absent -> None, which callers treat as "skip".
    assert trigram_schema(FakeConnection(None)) is None

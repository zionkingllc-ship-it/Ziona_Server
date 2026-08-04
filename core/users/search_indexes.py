"""Trigram search index management for Discover creator search.

`searchCreators` filters with `icontains`, which Django compiles on PostgreSQL to
`UPPER(col::text) LIKE UPPER('%term%')`. A leading wildcard cannot use a btree
index, so without a trigram GIN index this is a sequential scan — fine at small
user counts, expensive past roughly 50k users.

Shared by migration `users.0012_creator_search_trigram_indexes` and the
`create_search_indexes` management command, so the indexes can be added later
(after pg_trgm is enabled) without faking a migration.

Only DDL lives here — no model access — so it is safe for a migration to import.
"""

import logging

logger = logging.getLogger("core.users")

# (index name, indexed expression).
# The expression must mirror Django's icontains SQL exactly: Django uses UPPER()
# (not LOWER) for case-insensitive lookups, so an index on lower(col) would be
# silently ignored by the planner.
SEARCH_INDEXES = [
    ("idx_user_username_trgm", "upper(username::text)"),
    ("idx_user_full_name_trgm", "upper(full_name::text)"),
]


def trigram_schema(connection) -> str | None:
    """Return the schema pg_trgm is installed in, or None if it is absent.

    Detected rather than hardcoded: Supabase installs the extension into
    `extensions`, vanilla PostgreSQL into `public`. The `gin_trgm_ops` operator
    class must be schema-qualified to whichever is actually in use.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT n.nspname
            FROM pg_extension e
            JOIN pg_namespace n ON n.oid = e.extnamespace
            WHERE e.extname = 'pg_trgm'
            """
        )
        row = cursor.fetchone()
    return row[0] if row else None


def create_search_indexes(connection) -> bool:
    """Create the trigram indexes. Returns False if pg_trgm is unavailable.

    Never raises on a missing extension: migrations run during the Render build,
    and a missing extension must not fail the release.
    """
    if connection.vendor != "postgresql":
        return False

    # Best effort — the DB role may not be permitted to create extensions, in
    # which case it is enabled from the Supabase dashboard instead.
    try:
        with connection.cursor() as cursor:
            cursor.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    except Exception:
        logger.info("could_not_create_pg_trgm_extension_checking_for_existing")

    schema = trigram_schema(connection)
    if not schema:
        logger.warning(
            "pg_trgm_not_installed_skipping_search_indexes",
            extra={"hint": "Enable pg_trgm, then run manage.py create_search_indexes"},
        )
        return False

    with connection.cursor() as cursor:
        for name, expression in SEARCH_INDEXES:
            # CONCURRENTLY so the live users table is never locked for writes.
            cursor.execute(
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} "
                f'ON users USING gin ({expression} "{schema}".gin_trgm_ops)'
            )
    logger.info("search_indexes_created", extra={"trgm_schema": schema})
    return True


def drop_search_indexes(connection) -> None:
    """Drop the trigram indexes (migration reverse)."""
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        for name, _ in SEARCH_INDEXES:
            cursor.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")

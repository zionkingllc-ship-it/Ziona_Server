"""Create the trigram search indexes used by Discover creator search.

Run this after enabling the pg_trgm extension if migration
users.0012_creator_search_trigram_indexes skipped it (it skips instead of failing
a deploy when pg_trgm is not yet available):

    python manage.py create_search_indexes

Safe to run repeatedly — index creation uses IF NOT EXISTS.
"""

from django.core.management.base import BaseCommand
from django.db import connection

from core.users.search_indexes import (
    SEARCH_INDEXES,
    create_search_indexes,
    trigram_schema,
)


class Command(BaseCommand):
    help = "Create pg_trgm search indexes used by searchCreators (PostgreSQL only)."

    def handle(self, *args, **options):
        if connection.vendor != "postgresql":
            self.stdout.write(
                self.style.WARNING(
                    f"Database vendor is '{connection.vendor}' — trigram indexes are "
                    "PostgreSQL-only. Nothing to do."
                )
            )
            return

        if not trigram_schema(connection):
            self.stdout.write("pg_trgm is not installed yet. Attempting to enable it...")

        if create_search_indexes(connection):
            self.stdout.write(
                self.style.SUCCESS(
                    f"Search indexes are in place (pg_trgm in schema "
                    f"'{trigram_schema(connection)}')."
                )
            )
            for name, expression in SEARCH_INDEXES:
                self.stdout.write(f"  - {name} on {expression}")
            return

        self.stdout.write(
            self.style.ERROR(
                "pg_trgm is unavailable, so no indexes were created.\n"
                "Enable it first — Supabase: Database -> Extensions -> pg_trgm, or:\n"
                "  CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA extensions;\n"
                "then run this command again."
            )
        )

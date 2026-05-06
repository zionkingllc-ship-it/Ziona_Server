"""
Management command: seed_company_stats

Creates initial CompanyStat records with placeholder values. Idempotent.

Usage:
    python manage.py seed_company_stats
"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Seed initial CompanyStat records with placeholder values."

    _DEFAULTS = [
        {"key": "active_users", "value": 0, "display_value": "Growing daily"},
        {"key": "downloads", "value": 0, "display_value": "Coming soon"},
    ]

    def handle(self, *args, **options) -> None:
        from core.landing.models import CompanyStat

        created_count = 0
        for stat in self._DEFAULTS:
            _, created = CompanyStat.objects.get_or_create(
                key=stat["key"],
                defaults={
                    "value": stat["value"],
                    "display_value": stat["display_value"],
                },
            )
            if created:
                created_count += 1
                self.stdout.write(self.style.SUCCESS(f"  ✓ Created stat: {stat['key']}"))
            else:
                self.stdout.write(f"  – stat '{stat['key']}' already exists (skipped)")

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone. {created_count} stat(s) created, "
                f"{len(self._DEFAULTS) - created_count} skipped."
            )
        )

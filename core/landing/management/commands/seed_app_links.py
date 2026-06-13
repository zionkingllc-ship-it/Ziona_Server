"""
Management command: seed_app_links

Creates initial AppStoreLink records using env var URLs. Idempotent.

Usage:
    python manage.py seed_app_links
"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Seed iOS and Android app store link records."

    def handle(self, *args, **options) -> None:
        from django.conf import settings

        from core.landing.models import AppStoreLink

        links = [
            {
                "platform": "ios",
                "url": getattr(
                    settings,
                    "IOS_APP_STORE_URL",
                    "https://apps.apple.com/app/ziona",
                ),
            },
            {
                "platform": "android",
                "url": getattr(
                    settings,
                    "ANDROID_PLAY_STORE_URL",
                    "https://play.google.com/store/apps/ziona",
                ),
            },
        ]

        created_count = 0
        for link in links:
            obj, created = AppStoreLink.objects.get_or_create(
                platform=link["platform"],
                defaults={"url": link["url"], "is_active": True},
            )
            if created:
                created_count += 1
                self.stdout.write(
                    self.style.SUCCESS(f"  ✓ Created {link['platform']} link: {link['url']}")
                )
            else:
                self.stdout.write(
                    f"  – {link['platform']} link already exists: {obj.url} (skipped)"
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone. {created_count} link(s) created, {len(links) - created_count} skipped."
            )
        )

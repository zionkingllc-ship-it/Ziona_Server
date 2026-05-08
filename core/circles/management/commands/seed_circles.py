from django.core.management.base import BaseCommand
from django.db import transaction

from core.circles.constants import CIRCLE_RULES, DEFAULT_CIRCLES
from core.circles.models import Circle, CircleMembership, CircleRule
from core.users.models import User


class Command(BaseCommand):
    help = "Seeds the database with default Circles and standard rules"

    @transaction.atomic
    def handle(self, *args, **kwargs):
        self.stdout.write("Seeding initial Circles and Rules...")

        # Determine an admin user to own these default circles
        # We'll use the first superuser, or create a system user
        admin_user = User.objects.filter(is_superuser=True).first()
        if not admin_user:
            admin_user, created = User.objects.get_or_create(
                email="system@ziona.app",
                defaults={
                    "first_name": "Ziona",
                    "last_name": "System",
                    "is_active": True,
                    "is_staff": True,
                    "is_superuser": True,
                },
            )
            if created:
                admin_user.set_unusable_password()
                admin_user.save()

        # Seed Rules
        for rule_data in CIRCLE_RULES:
            CircleRule.objects.get_or_create(
                circle__isnull=True,
                rule_number=rule_data["rule_number"],
                is_default=True,
                defaults={
                    "title": rule_data["title"],
                    "description": rule_data["description"],
                },
            )
        self.stdout.write(
            self.style.SUCCESS(f"Successfully seeded {len(CIRCLE_RULES)} default Circle Rules.")
        )

        # Seed Circles
        seeded_count = 0
        for circle_data in DEFAULT_CIRCLES:
            circle, created = Circle.objects.get_or_create(
                name=circle_data["name"],
                defaults={
                    "description": circle_data["description"],
                    "cover_image": circle_data["cover_image"],
                    "is_active": True,
                },
            )
            if created:
                seeded_count += 1
                # Add the system user as an admin to this default circle
                CircleMembership.objects.create(circle=circle, user=admin_user, role="admin")

        self.stdout.write(
            self.style.SUCCESS(f"Successfully seeded {seeded_count} new default Circles.")
        )
        self.stdout.write(self.style.SUCCESS("Done seeding Circles!"))

"""
Management command: setup_test_admin
=====================================
Provisions a persistent superuser account for Frontend/QA testing.

IMPORTANT: This command is intended for Development and Staging environments ONLY.
           Never run this against a Production database.

Usage:
    python manage.py setup_test_admin
"""

import warnings

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from core.users.models import UserRole

# -- Credentials (Dev/Staging ONLY) ------------------------------------------
ADMIN_EMAIL = "admin@ziona.app"
ADMIN_PASSWORD = "Admin00"
ADMIN_USERNAME = "ziona_admin"
# ----------------------------------------------------------------------------


class Command(BaseCommand):
    """Idempotently provisions the shared admin test account.

    First run  -> creates the account and prints a success message.
    Subsequent -> ensures all required flags are correct, resets password, and confirms.

    This command is safe to run after every `migrate` or `flush`.
    """

    help = "[DEV/STAGING ONLY] Provision the shared admin test account for Frontend/QA access."

    def handle(self, *args, **kwargs):
        """Entry point for the management command."""
        self._warn_if_production()

        user_model = get_user_model()
        email_normalised = user_model.objects.normalize_email(ADMIN_EMAIL)

        # Use all_objects so a previously soft-deleted account is also found
        # and can be properly restored rather than triggering an IntegrityError.
        user, created = user_model.all_objects.get_or_create(
            email=email_normalised,
            defaults={
                "username": ADMIN_USERNAME,
                "full_name": "Ziona Admin",
                "role": UserRole.ADMIN,
                "is_staff": True,
                "is_superuser": True,
                "is_active": True,
                "is_email_verified": True,
                "deleted_at": None,
            },
        )

        if created:
            user.set_password(ADMIN_PASSWORD)
            user.save(update_fields=["password"])
            self.stdout.write(
                self.style.SUCCESS(f"Successfully created admin account: {email_normalised}")
            )
        else:
            # Idempotency: enforce all required flags and refresh the password.
            updates = []

            fields_to_enforce = {
                "is_staff": True,
                "is_superuser": True,
                "is_active": True,
                "is_email_verified": True,
                "role": UserRole.ADMIN,
                "deleted_at": None,
            }

            for field, expected in fields_to_enforce.items():
                if getattr(user, field) != expected:
                    setattr(user, field, expected)
                    updates.append(field)

            user.set_password(ADMIN_PASSWORD)
            updates.append("password")

            user.save(update_fields=updates + ["updated_at"])
            self.stdout.write(
                self.style.SUCCESS(f"Admin account updated/verified: {email_normalised}")
            )

        self.stdout.write(
            self.style.WARNING(
                "\n[REMINDER] This account is for Dev/Staging only. "
                "Do NOT run this command against Production."
            )
        )

    def _warn_if_production(self):
        """Emit a loud warning if DEBUG=False, which likely means a production env."""
        from django.conf import settings

        if not settings.DEBUG:
            warnings.warn(
                "\n\n WARNING: setup_test_admin is running with DEBUG=False. "
                "This looks like a Production environment. "
                "Abort immediately if this is unintentional!\n",
                stacklevel=2,
            )
            self.stdout.write(
                self.style.ERROR(
                    "\n DEBUG=False detected - this may be a Production environment!"
                    " Proceeding anyway, but you have been warned.\n"
                )
            )

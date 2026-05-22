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

from core.users.models import UserRole, UserStatus

# -- Credentials (Dev/Staging ONLY) ------------------------------------------
ADMIN_PASSWORD = "Admin00"
ADMIN_ACCOUNTS = (
    {
        "email": "admin@ziona.app",
        "username": "ziona_admin",
        "full_name": "Ziona Admin",
    },
    {
        "email": "info@zionking.org",
        "username": "zionking_info_admin",
        "full_name": "ZionKing Info Admin",
    },
    {
        "email": "support@ziona.app",
        "username": "ziona_support_admin",
        "full_name": "Ziona Support Admin",
    },
)
# ----------------------------------------------------------------------------


class Command(BaseCommand):
    """Idempotently provisions shared admin dashboard test accounts.

    First run  -> creates the accounts and prints success messages.
    Subsequent -> ensures all required flags are correct, resets passwords, and confirms.

    This command is safe to run after every `migrate` or `flush`.
    """

    help = "[DEV/STAGING ONLY] Provision shared admin test accounts for Frontend/QA access."

    def handle(self, *args, **kwargs):
        """Entry point for the management command."""
        self._warn_if_production()

        user_model = get_user_model()
        for account in ADMIN_ACCOUNTS:
            self._provision_admin_account(user_model, account)

        self.stdout.write(
            self.style.WARNING(
                "\n[REMINDER] This account is for Dev/Staging only. "
                "Do NOT run this command against Production."
            )
        )

    def _provision_admin_account(self, user_model, account):
        email_normalised = user_model.objects.normalize_email(account["email"])

        # Use all_objects so a previously soft-deleted account is also found
        # and can be properly restored rather than triggering an IntegrityError.
        user, created = user_model.all_objects.get_or_create(
            email=email_normalised,
            defaults={
                "username": account["username"],
                "full_name": account["full_name"],
                "role": UserRole.ADMIN,
                "status": UserStatus.ACTIVE,
                "is_staff": True,
                "is_superuser": True,
                "is_active": True,
                "is_email_verified": True,
                "deleted_at": None,
                "warned_at": None,
                "suspended_at": None,
                "suspension_reason": "",
            },
        )

        if created:
            user.set_password(ADMIN_PASSWORD)
            user.save(update_fields=["password"])
            self.stdout.write(
                self.style.SUCCESS(f"Successfully created admin account: {email_normalised}")
            )
            return

        # Idempotency: enforce all required flags and refresh the password.
        updates = []

        fields_to_enforce = {
            "is_staff": True,
            "is_superuser": True,
            "is_active": True,
            "is_email_verified": True,
            "role": UserRole.ADMIN,
            "status": UserStatus.ACTIVE,
            "deleted_at": None,
            "warned_at": None,
            "suspended_at": None,
            "suspension_reason": "",
        }

        for field, expected in fields_to_enforce.items():
            if getattr(user, field) != expected:
                setattr(user, field, expected)
                updates.append(field)

        user.set_password(ADMIN_PASSWORD)
        updates.append("password")

        user.save(update_fields=updates + ["updated_at"])
        self.stdout.write(self.style.SUCCESS(f"Admin account updated/verified: {email_normalised}"))

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

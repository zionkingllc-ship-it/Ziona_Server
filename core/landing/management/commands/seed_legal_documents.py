"""
Management command: seed_legal_documents

Creates initial Privacy Policy, Terms of Service, and Community Guidelines
with placeholder content and version "1.0". Idempotent via get_or_create.

Usage:
    python manage.py seed_legal_documents
"""

from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Seed initial legal documents (Privacy Policy, ToS, Community Guidelines)."

    _DOCUMENTS = [
        {
            "type": "privacy_policy",
            "version": "1.0",
            "content": (
                "# Privacy Policy\n\n"
                "*Version 1.0 — Placeholder content.*\n\n"
                "This document will be updated with the full Privacy Policy before launch.\n\n"
                "## Data We Collect\n\n"
                "We collect information you provide directly, such as your name, email "
                "address, and any content you create on the platform.\n\n"
                "## How We Use Your Data\n\n"
                "We use your data to provide and improve the Ziona platform services.\n\n"
                "## Contact\n\n"
                "For privacy questions, contact support@ziona.app."
            ),
        },
        {
            "type": "terms_of_service",
            "version": "1.0",
            "content": (
                "# Terms of Service\n\n"
                "*Version 1.0 — Placeholder content.*\n\n"
                "This document will be updated with the full Terms of Service before launch.\n\n"
                "## Acceptance\n\n"
                "By using Ziona, you agree to these terms.\n\n"
                "## Community Standards\n\n"
                "All users must adhere to our Community Guidelines and conduct themselves "
                "in a manner consistent with the values of the platform.\n\n"
                "## Contact\n\n"
                "For legal questions, contact support@ziona.app."
            ),
        },
        {
            "type": "community_guidelines",
            "version": "1.0",
            "content": (
                "# Community Guidelines\n\n"
                "*Version 1.0 — Placeholder content.*\n\n"
                "This document will be updated with the full Community Guidelines before launch.\n\n"
                "## Our Values\n\n"
                "Ziona is a faith-based platform built on respect, love, and the teachings "
                "of Christ. All members are expected to uphold these values.\n\n"
                "## Core Rules\n\n"
                "1. Treat all members with respect and dignity.\n"
                "2. Share content that glorifies Christ and builds the community.\n"
                "3. No hate speech, harassment, or harmful content.\n"
                "4. No spam or unsolicited promotion.\n\n"
                "## Reporting\n\n"
                "Use the in-app report feature to flag violations."
            ),
        },
    ]

    def handle(self, *args, **options) -> None:
        from core.landing.models import LegalDocument

        created_count = 0
        for doc_data in self._DOCUMENTS:
            doc_type = doc_data["type"]
            version = doc_data["version"]
            content = doc_data["content"]

            # get_or_create on (type, version) — idempotent
            doc, created = LegalDocument.objects.get_or_create(
                type=doc_type,
                version=version,
                defaults={
                    "content": content,
                    "is_active": True,
                    "published_at": timezone.now(),
                },
            )

            if created:
                # Deactivate any other active doc of the same type
                LegalDocument.objects.filter(type=doc_type, is_active=True).exclude(
                    pk=doc.pk
                ).update(is_active=False)

                created_count += 1
                self.stdout.write(self.style.SUCCESS(f"  ✓ Created {doc_type} v{version}"))
            else:
                # Ensure it is marked active (idempotent re-run safety)
                if not doc.is_active:
                    doc.is_active = True
                    doc.save(update_fields=["is_active"])
                self.stdout.write(f"  – {doc_type} v{version} already exists (skipped)")

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone. {created_count} document(s) created, "
                f"{len(self._DOCUMENTS) - created_count} skipped."
            )
        )

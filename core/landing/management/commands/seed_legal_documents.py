"""
Management command: seed_legal_documents

Publishes the current Privacy Policy, Terms of Use, and Community Guidelines.
The canonical mobile rendering contract is a public PDF URL hosted in GCS.

Usage:
    python manage.py seed_legal_documents
"""

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

LEGAL_DOCUMENT_VERSION = "2026.05.27"
LEGAL_DOCUMENT_TYPE = "application/pdf"


class Command(BaseCommand):
    help = "Seed current legal documents as public PDF-backed records."

    _DOCUMENTS = [
        {
            "type": "privacy_policy",
            "version": LEGAL_DOCUMENT_VERSION,
            "document_url": "{base_url}/privacy-policy-v{version}.pdf",
        },
        {
            "type": "terms_of_service",
            "version": LEGAL_DOCUMENT_VERSION,
            "document_url": "{base_url}/terms-of-use-v{version}.pdf",
        },
        {
            "type": "community_guidelines",
            "version": LEGAL_DOCUMENT_VERSION,
            "document_url": "{base_url}/community-guidelines-v{version}.pdf",
        },
    ]

    def handle(self, *args, **options) -> None:
        from core.landing.models import LegalDocument

        created_count = 0
        updated_count = 0
        activated_count = 0

        for doc_data in self._DOCUMENTS:
            result = self._publish_document(LegalDocument, doc_data)
            created_count += result["created"]
            updated_count += result["updated"]
            activated_count += result["activated"]

        self.stdout.write(
            self.style.SUCCESS(
                "\nDone. "
                f"{created_count} document(s) created, "
                f"{updated_count} updated, "
                f"{activated_count} activated."
            )
        )

    def _publish_document(self, legal_document_model, doc_data: dict) -> dict:
        doc_type = doc_data["type"]
        version = doc_data["version"]
        document_url = doc_data["document_url"].format(
            base_url=settings.LEGAL_DOCUMENT_BASE_URL.rstrip("/"),
            version=version,
        )
        now = timezone.now()

        with transaction.atomic():
            doc, created = legal_document_model.objects.get_or_create(
                type=doc_type,
                version=version,
                defaults={
                    "content": "",
                    "document_url": document_url,
                    "document_type": LEGAL_DOCUMENT_TYPE,
                    "is_active": False,
                    "published_at": now,
                },
            )

            updated = False
            update_fields = []
            if doc.content:
                doc.content = ""
                update_fields.append("content")
                updated = True
            if doc.document_url != document_url:
                doc.document_url = document_url
                update_fields.append("document_url")
                updated = True
            if doc.document_type != LEGAL_DOCUMENT_TYPE:
                doc.document_type = LEGAL_DOCUMENT_TYPE
                update_fields.append("document_type")
                updated = True
            if doc.published_at is None:
                doc.published_at = now
                update_fields.append("published_at")
                updated = True

            if update_fields:
                doc.save(update_fields=update_fields)

            legal_document_model.objects.filter(type=doc_type, is_active=True).exclude(
                pk=doc.pk
            ).update(is_active=False)

            activated = False
            if not doc.is_active:
                doc.is_active = True
                doc.save(update_fields=["is_active"])
                activated = True

        if created:
            self.stdout.write(self.style.SUCCESS(f"  - Created {doc_type} v{version}"))
        elif updated:
            self.stdout.write(self.style.SUCCESS(f"  - Updated {doc_type} v{version}"))
        elif activated:
            self.stdout.write(self.style.SUCCESS(f"  - Activated {doc_type} v{version}"))
        else:
            self.stdout.write(f"  - {doc_type} v{version} already active")

        return {
            "created": int(created),
            "updated": int(updated),
            "activated": int(activated),
        }

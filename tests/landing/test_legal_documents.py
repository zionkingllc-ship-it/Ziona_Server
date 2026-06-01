from django.core.management import call_command

from config.graphql_schema import schema
from core.landing.models import LegalDocument, LegalDocumentType

CURRENT_VERSION = "2026.05.27"
BASE_URL = "https://storage.googleapis.com/ziona-media-dev/legal-documents"


def test_seed_legal_documents_publishes_current_legal_copy():
    call_command("seed_legal_documents")

    privacy = LegalDocument.objects.get(
        type=LegalDocumentType.PRIVACY_POLICY,
        is_active=True,
    )
    terms = LegalDocument.objects.get(
        type=LegalDocumentType.TERMS_OF_SERVICE,
        is_active=True,
    )
    guidelines = LegalDocument.objects.get(
        type=LegalDocumentType.COMMUNITY_GUIDELINES,
        is_active=True,
    )

    assert privacy.version == CURRENT_VERSION
    assert terms.version == CURRENT_VERSION
    assert guidelines.version == CURRENT_VERSION

    assert privacy.content == ""
    assert terms.content == ""
    assert guidelines.content == ""

    assert privacy.document_url == f"{BASE_URL}/privacy-policy-v{CURRENT_VERSION}.pdf"
    assert terms.document_url == f"{BASE_URL}/terms-of-use-v{CURRENT_VERSION}.pdf"
    assert guidelines.document_url == f"{BASE_URL}/community-guidelines-v{CURRENT_VERSION}.pdf"

    for doc in (privacy, terms, guidelines):
        assert doc.document_type == "application/pdf"
        assert "[Insert Date]" not in doc.content
        assert "Placeholder content" not in doc.content


def test_seed_legal_documents_is_idempotent():
    call_command("seed_legal_documents")
    call_command("seed_legal_documents")

    for doc_type in LegalDocumentType.values:
        assert LegalDocument.objects.filter(type=doc_type, version=CURRENT_VERSION).count() == 1
        assert LegalDocument.objects.filter(type=doc_type, is_active=True).count() == 1


def test_seed_legal_documents_replaces_existing_active_placeholder():
    old_doc = LegalDocument.objects.create(
        type=LegalDocumentType.PRIVACY_POLICY,
        version="1.0",
        content="# Privacy Policy\n\nPlaceholder content.",
        is_active=True,
    )

    call_command("seed_legal_documents")

    old_doc.refresh_from_db()
    current_doc = LegalDocument.objects.get(
        type=LegalDocumentType.PRIVACY_POLICY,
        is_active=True,
    )

    assert old_doc.is_active is False
    assert current_doc.version == CURRENT_VERSION
    assert current_doc.content == ""
    assert current_doc.document_url == f"{BASE_URL}/privacy-policy-v{CURRENT_VERSION}.pdf"
    assert current_doc.document_type == "application/pdf"


def test_legal_document_queries_return_pdf_metadata():
    call_command("seed_legal_documents")

    result = schema.execute_sync(
        """
        query LegalDocuments {
          privacyPolicy {
            version
            lastUpdated
            content
            documentUrl
            documentType
          }
          termsOfService {
            version
            documentUrl
            documentType
          }
          communityGuidelines {
            version
            documentUrl
            documentType
          }
        }
        """
    )

    assert result.errors is None
    data = result.data
    assert data["privacyPolicy"]["content"] == ""
    assert data["privacyPolicy"]["documentType"] == "application/pdf"
    assert data["privacyPolicy"]["documentUrl"] == (
        f"{BASE_URL}/privacy-policy-v{CURRENT_VERSION}.pdf"
    )
    assert data["termsOfService"]["documentUrl"] == (
        f"{BASE_URL}/terms-of-use-v{CURRENT_VERSION}.pdf"
    )
    assert data["communityGuidelines"]["documentUrl"] == (
        f"{BASE_URL}/community-guidelines-v{CURRENT_VERSION}.pdf"
    )

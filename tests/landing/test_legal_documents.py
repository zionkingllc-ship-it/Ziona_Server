from django.core.management import call_command

from core.landing.models import LegalDocument, LegalDocumentType

CURRENT_VERSION = "2026.05.27"


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

    assert "Effective Date: May 27, 2026" in privacy.content
    assert "Effective Date: May 27, 2026" in terms.content
    assert "State of Maryland" in terms.content
    assert "Gospel of John 1:5" in guidelines.content

    for doc in (privacy, terms, guidelines):
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
    assert "Effective Date: May 27, 2026" in current_doc.content

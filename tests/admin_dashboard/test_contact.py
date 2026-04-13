import pytest

from core.admin_dashboard.contact_services import ContactService
from core.admin_dashboard.models import ContactMessage


@pytest.mark.django_db
def test_list_contacts(authenticated_admin):
    ContactMessage.objects.create(
        name="Test User", email="test@example.com", message="Help me", status="pending"
    )
    result = ContactService.list_contacts("pending", None, 1, 10)
    assert result["total_count"] == 1
    assert len(result["contacts"]) == 1


@pytest.mark.django_db
def test_update_contact_status(authenticated_admin):
    contact = ContactMessage.objects.create(
        name="Test User", email="test@example.com", message="Help me", status="pending"
    )
    # Using string enum matching what schema uses
    result = ContactService.update_contact_status(
        str(contact.id), "resolved", authenticated_admin["user"]
    )
    assert isinstance(result, dict)

import pytest

from core.admin_dashboard.contact_services import ContactService
from core.admin_dashboard.models import ContactMessage
from core.landing.services import ContactService as LandingContactService


@pytest.mark.django_db
def test_list_contacts(authenticated_admin):
    ContactMessage.objects.create(
        name="Test User", email="test@example.com", message="Help me", status="pending"
    )
    result = ContactService.list_contacts("pending", None, 1, 10)
    assert result["total_count"] == 1
    assert len(result["contacts"]) == 1


@pytest.mark.django_db
def test_contact_serves_live_requester_avatar(authenticated_admin):
    from core.users.models import User

    user = User.objects.create_user(
        email="requester@example.com", username="requester", password="Pass123!"
    )
    ContactMessage.objects.create(
        name="Stale Name",
        email="requester@example.com",
        message="Help",
        status="pending",
        requester_user=user,
    )

    # Profile updates AFTER the ticket was filed.
    user.avatar_url = "https://cdn.example.com/new-avatar.jpg"
    user.username = "requester_renamed"
    user.save(update_fields=["avatar_url", "username"])

    result = ContactService.list_contacts("pending", None, 1, 10)
    contact = result["contacts"][0]

    # Avatar + username come live from the profile, not the ticket snapshot.
    assert contact["requester_avatar_url"] == "https://cdn.example.com/new-avatar.jpg"
    assert contact["requester_username"] == "requester_renamed"


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


@pytest.mark.django_db
def test_landing_contact_is_visible_in_admin_contact_queue(authenticated_admin, monkeypatch):
    monkeypatch.setattr(
        "core.emails.services.EmailService.send_contact_auto_reply",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "core.emails.services.EmailService.send_internal_contact_notification",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr("core.landing.services._check_rate_limit", lambda *args, **kwargs: None)

    LandingContactService.submit(
        brand="ziona",
        name="Mobile User",
        email="mobile@example.com",
        message="I need help with the mobile app.",
        ip_address="203.0.113.10",
    )

    contact = ContactMessage.objects.get(email="mobile@example.com")

    assert contact.source == "landing_page"
    assert contact.brand == "ZIONA"
    assert contact.message == "I need help with the mobile app."

"""Contact source/origin: label logic + capture across the submission paths."""

import json

import pytest

from core.admin_dashboard.contact_services import (
    ContactService,
    _contact_to_dict,
    contact_source_label,
)
from core.admin_dashboard.models import ContactMessage
from core.landing.services import ContactService as LandingContactService


@pytest.mark.parametrize(
    "source,brand,origin_url,platform,expected",
    [
        # Observed web origin → friendly name (authoritative; wins over brand/platform).
        ("landing_page", "ZIONA", "https://zionking.org", "", "zionking.org"),
        ("mobile_app", "", "https://admin.ziona.app", "", "Admin dashboard"),
        ("mobile_app", "", "https://unknown.example.com", "", "unknown.example.com"),
        # No origin → landing brand fallback (covers pre-feature rows).
        ("landing_page", "ZIONKING", "", "", "zionking.org"),
        ("landing_page", "ZIONA", "", "", "ziona.app"),
        # No origin → mobile platform suffix.
        ("mobile_help", "ZIONA", "", "ios", "Mobile app (iOS)"),
        ("mobile_app", "", "", "android", "Mobile app (Android)"),
        ("mobile_app", "", "", "", "Mobile app"),
    ],
)
def test_contact_source_label(source, brand, origin_url, platform, expected):
    assert contact_source_label(source, brand, origin_url, platform) == expected


@pytest.mark.django_db
def test_submit_message_stores_and_normalizes_platform():
    result = ContactService.submit_message(
        name="U", email="ios@example.com", message="hi", platform="iOS"
    )
    contact = ContactMessage.objects.get(id=result["contact_id"])
    assert contact.platform == "ios"  # normalized to lowercase
    assert _contact_to_dict(contact)["source_label"] == "Mobile app (iOS)"


@pytest.mark.django_db
def test_submit_message_ignores_bogus_platform():
    result = ContactService.submit_message(
        name="U", email="bogus@example.com", message="hi", platform="windows-phone"
    )
    contact = ContactMessage.objects.get(id=result["contact_id"])
    assert contact.platform == ""  # unrecognized → dropped
    assert _contact_to_dict(contact)["source_label"] == "Mobile app"


@pytest.mark.django_db
def test_submit_message_captures_origin_url():
    result = ContactService.submit_message(
        name="U", email="web@example.com", message="hi", origin_url="https://ziona.app"
    )
    contact = ContactMessage.objects.get(id=result["contact_id"])
    assert contact.origin_url == "https://ziona.app"
    assert _contact_to_dict(contact)["source_label"] == "ziona.app"


@pytest.mark.django_db
def test_landing_submission_captures_origin(monkeypatch):
    monkeypatch.setattr(
        "core.emails.services.EmailService.send_contact_auto_reply", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "core.emails.services.EmailService.send_internal_contact_notification",
        lambda *a, **k: None,
    )
    monkeypatch.setattr("core.landing.services._check_rate_limit", lambda *a, **k: None)

    LandingContactService.submit(
        brand="zionking",
        name="Visitor",
        email="visitor@example.com",
        message="Tell me more.",
        ip_address="203.0.113.10",
        origin_url="https://zionking.org",
    )

    contact = ContactMessage.objects.get(email="visitor@example.com")
    assert contact.source == "landing_page"
    assert contact.origin_url == "https://zionking.org"
    assert _contact_to_dict(contact)["source_label"] == "zionking.org"


@pytest.mark.django_db
def test_submit_contact_message_mutation_captures_declared_platform(api_client):
    # Native mobile app: declares platform, sends no Origin header.
    response = api_client.post(
        "/graphql/",
        data=json.dumps(
            {
                "query": """
                mutation ($message: String!, $name: String, $email: String, $platform: String) {
                  submitContactMessage(
                    message: $message, name: $name, email: $email, platform: $platform
                  ) { success contactId error { code } }
                }
                """,
                "variables": {
                    "message": "help",
                    "name": "Mobile User",
                    "email": "native@example.com",
                    "platform": "android",
                },
            }
        ),
        content_type="application/json",
    )
    content = json.loads(response.content)
    assert "errors" not in content, content.get("errors")
    assert content["data"]["submitContactMessage"]["success"] is True

    contact = ContactMessage.objects.get(email="native@example.com")
    assert contact.platform == "android"
    assert contact.origin_url == ""  # native client sends no Origin
    assert _contact_to_dict(contact)["source_label"] == "Mobile app (Android)"

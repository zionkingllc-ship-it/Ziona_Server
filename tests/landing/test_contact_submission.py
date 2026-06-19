import json

import pytest

from core.admin_dashboard.models import ContactMessage
from core.landing.models import ContactSubmission


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("brand", "email"),
    [
        ("ZIONA", "ziona-contact@example.com"),
        ("ZIONKING", "zionking-contact@example.com"),
    ],
)
def test_submit_contact_mutation_persists_submission_and_admin_contact(
    api_client,
    monkeypatch,
    brand,
    email,
):
    monkeypatch.setattr(
        "core.emails.services.EmailService.send_contact_auto_reply",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "core.emails.services.EmailService.send_internal_contact_notification",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr("core.landing.services._check_rate_limit", lambda *args, **kwargs: None)

    response = api_client.post(
        "/graphql/",
        data=json.dumps(
            {
                "query": """
                mutation SubmitContact(
                  $brand: ContactBrand!,
                  $name: String!,
                  $email: String!,
                  $message: String!,
                  $honeypot: String!
                ) {
                  submitContact(
                    brand: $brand,
                    name: $name,
                    email: $email,
                    message: $message,
                    honeypot: $honeypot
                  ) {
                    success
                    ticketId
                    error {
                      code
                      message
                    }
                  }
                }
                """,
                "variables": {
                    "brand": brand,
                    "name": "Landing Visitor",
                    "email": email,
                    "message": "I would love to learn more about your platform.",
                    "honeypot": "",
                },
            }
        ),
        content_type="application/json",
    )

    content = json.loads(response.content)
    assert response.status_code == 200, content
    assert "errors" not in content, content.get("errors")

    payload = content["data"]["submitContact"]
    assert payload["success"] is True
    assert payload["ticketId"]
    assert payload["error"] is None

    submission = ContactSubmission.objects.get(email=email)
    admin_contact = ContactMessage.objects.get(email=email)

    assert submission.brand == brand
    assert admin_contact.brand == brand
    assert admin_contact.source == "landing_page"
    assert admin_contact.message == "I would love to learn more about your platform."

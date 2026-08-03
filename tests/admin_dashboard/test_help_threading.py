"""Admin chat threading: the same user's messages stay in one open thread.

Regression for the fractured-chat bug — submitHelpMessage used to create a new
ContactMessage on every call. It now reuses the user's open thread (Option B:
a new thread starts only after the previous is resolved).
"""

import json

import pytest
from django.test import Client

from core.admin_dashboard.contact_services import ContactService
from core.admin_dashboard.models import ContactMessage, ContactStatus


@pytest.mark.django_db
def test_two_submits_from_same_user_reuse_one_open_thread(create_user):
    user = create_user()

    first = ContactService.submit_help_message(message="first message", user=user)
    second = ContactService.submit_help_message(message="second message", user=user)

    assert first["contact_id"] == second["contact_id"]  # same thread
    assert ContactMessage.objects.filter(requester_user=user).count() == 1

    contact = ContactMessage.objects.get(requester_user=user)
    messages = list(contact.conversation_messages.order_by("created_at"))
    assert [m.message for m in messages] == ["first message", "second message"]
    assert all(m.sender_type == "USER" for m in messages)


@pytest.mark.django_db
def test_in_progress_thread_is_still_reused(create_user):
    user = create_user()
    first = ContactService.submit_help_message(message="first", user=user)
    ContactMessage.objects.filter(id=first["contact_id"]).update(status=ContactStatus.IN_PROGRESS)

    second = ContactService.submit_help_message(message="follow-up", user=user)

    assert second["contact_id"] == first["contact_id"]
    assert ContactMessage.objects.filter(requester_user=user).count() == 1


@pytest.mark.django_db
def test_new_thread_starts_after_previous_is_resolved(create_user):
    user = create_user()
    first = ContactService.submit_help_message(message="first issue", user=user)
    ContactMessage.objects.filter(id=first["contact_id"]).update(status=ContactStatus.RESOLVED)

    second = ContactService.submit_help_message(message="a brand new issue", user=user)

    assert second["contact_id"] != first["contact_id"]
    assert ContactMessage.objects.filter(requester_user=user).count() == 2


@pytest.mark.django_db
def test_submit_help_message_mutation_coalesces_into_one_thread(authenticated_user):
    client = Client()
    client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {authenticated_user['access_token']}"
    query = """
    mutation ($m: String!) {
      submitHelpMessage(message: $m) {
        success
        contact { id }
        error { code }
      }
    }
    """

    def submit(msg):
        resp = client.post(
            "/graphql/",
            data=json.dumps({"query": query, "variables": {"m": msg}}),
            content_type="application/json",
        )
        body = json.loads(resp.content)
        assert "errors" not in body, body.get("errors")
        return body["data"]["submitHelpMessage"]

    first = submit("hello support")
    second = submit("still here, following up")

    assert first["success"] and second["success"]
    assert first["contact"]["id"] == second["contact"]["id"]
    assert ContactMessage.objects.filter(requester_user=authenticated_user["user"]).count() == 1

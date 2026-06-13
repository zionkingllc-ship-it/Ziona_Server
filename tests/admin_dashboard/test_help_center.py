import json

import pytest
from django.test import Client

from core.admin_dashboard.models import ContactMessage


@pytest.mark.django_db
def test_help_categories_query_returns_seeded_help_content(api_client):
    response = api_client.post(
        "/graphql/",
        data=json.dumps(
            {
                "query": """
                query HelpCategories {
                  helpCategories {
                    slug
                    title
                    articleCount
                    articles {
                      slug
                      categorySlug
                    }
                  }
                }
                """
            }
        ),
        content_type="application/json",
    )

    content = json.loads(response.content)
    assert response.status_code == 200, content
    assert "errors" not in content, content.get("errors")

    categories = content["data"]["helpCategories"]
    assert categories
    assert any(category["slug"] == "account-management" for category in categories)
    assert all(category["articleCount"] >= 1 for category in categories)


@pytest.mark.django_db
def test_authenticated_help_message_is_visible_in_my_help_conversations_and_admin_queue(
    authenticated_user, authenticated_admin
):
    user_client = Client()
    user_client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {authenticated_user['access_token']}"

    submit_response = user_client.post(
        "/graphql/",
        data=json.dumps(
            {
                "query": """
                mutation SubmitHelpMessage($message: String!, $categorySlug: String) {
                  submitHelpMessage(message: $message, categorySlug: $categorySlug) {
                    success
                    contact {
                      id
                      topic
                      status
                      messages {
                        senderType
                        senderName
                        message
                      }
                    }
                    error {
                      code
                      message
                    }
                  }
                }
                """,
                "variables": {
                    "message": "I need help getting back into my account.",
                    "categorySlug": "account-management",
                },
            }
        ),
        content_type="application/json",
    )

    submit_content = json.loads(submit_response.content)
    assert submit_response.status_code == 200, submit_content
    assert "errors" not in submit_content, submit_content.get("errors")
    payload = submit_content["data"]["submitHelpMessage"]
    assert payload["success"] is True
    assert payload["contact"]["topic"] == "Account management"
    assert payload["contact"]["messages"][0]["senderType"] == "USER"

    contact = ContactMessage.objects.get(id=payload["contact"]["id"])
    assert contact.requester_user == authenticated_user["user"]
    assert contact.source == "mobile_help"
    assert contact.topic == "Account management"

    conversations_response = user_client.post(
        "/graphql/",
        data=json.dumps(
            {
                "query": """
                query MyHelpConversations {
                  myHelpConversations {
                    id
                    topic
                    status
                    messages {
                      senderType
                      message
                    }
                  }
                }
                """
            }
        ),
        content_type="application/json",
    )

    conversations_content = json.loads(conversations_response.content)
    assert conversations_response.status_code == 200, conversations_content
    assert "errors" not in conversations_content, conversations_content.get("errors")
    conversations = conversations_content["data"]["myHelpConversations"]
    assert len(conversations) == 1
    assert conversations[0]["id"] == str(contact.id)

    admin_client = Client()
    admin_client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {authenticated_admin['access_token']}"
    admin_response = admin_client.post(
        "/graphql/",
        data=json.dumps(
            {
                "query": """
                query AdminContacts {
                  adminContacts {
                    contacts {
                      id
                      email
                      source
                      topic
                      requesterUsername
                    }
                  }
                }
                """
            }
        ),
        content_type="application/json",
    )

    admin_content = json.loads(admin_response.content)
    assert admin_response.status_code == 200, admin_content
    assert "errors" not in admin_content, admin_content.get("errors")
    contacts = admin_content["data"]["adminContacts"]["contacts"]
    assert any(
        item["id"] == str(contact.id)
        and item["source"] == "mobile_help"
        and item["topic"] == "Account management"
        and item["requesterUsername"] == authenticated_user["user"].username
        for item in contacts
    )


@pytest.mark.django_db
def test_submit_contact_message_backfills_authenticated_help_flow(authenticated_user):
    client = Client()
    client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {authenticated_user['access_token']}"

    response = client.post(
        "/graphql/",
        data=json.dumps(
            {
                "query": """
                mutation SubmitContactMessage($message: String!, $categorySlug: String) {
                  submitContactMessage(message: $message, categorySlug: $categorySlug) {
                    success
                    contactId
                    contact {
                      topic
                      status
                    }
                    error {
                      code
                      message
                    }
                  }
                }
                """,
                "variables": {
                    "message": "Something is wrong with my notifications.",
                    "categorySlug": "posts-and-circles",
                },
            }
        ),
        content_type="application/json",
    )

    content = json.loads(response.content)
    assert response.status_code == 200, content
    assert "errors" not in content, content.get("errors")
    payload = content["data"]["submitContactMessage"]
    assert payload["success"] is True
    assert payload["contact"]["topic"] == "Posts and circles"
    assert ContactMessage.objects.filter(
        id=payload["contactId"],
        requester_user=authenticated_user["user"],
        source="mobile_help",
    ).exists()


@pytest.mark.django_db
def test_resolve_help_conversation_marks_thread_resolved(authenticated_user):
    contact = ContactMessage.objects.create(
        name=authenticated_user["user"].full_name or authenticated_user["user"].username,
        email=authenticated_user["user"].email,
        message="Thanks, I got back in.",
        requester_user=authenticated_user["user"],
        topic="Account management",
        source="mobile_help",
        brand="ZIONA",
    )

    client = Client()
    client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {authenticated_user['access_token']}"
    response = client.post(
        "/graphql/",
        data=json.dumps(
            {
                "query": """
                mutation ResolveHelpConversation($contactId: String!) {
                  resolveHelpConversation(contactId: $contactId) {
                    success
                    contact {
                      id
                      status
                    }
                    error {
                      code
                      message
                    }
                  }
                }
                """,
                "variables": {"contactId": str(contact.id)},
            }
        ),
        content_type="application/json",
    )

    content = json.loads(response.content)
    assert response.status_code == 200, content
    assert "errors" not in content, content.get("errors")
    payload = content["data"]["resolveHelpConversation"]
    assert payload["success"] is True
    assert payload["contact"]["status"] == "resolved"

    contact.refresh_from_db()
    assert contact.status == "resolved"

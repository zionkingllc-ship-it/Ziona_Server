import json

import pytest
from django.test import Client


@pytest.mark.django_db
def test_notification_preferences_accepts_bearer_token(authenticated_user):
    client = Client()
    client.defaults["HTTP_AUTHORIZATION"] = f'Bearer {authenticated_user["access_token"]}'

    response = client.post(
        "/graphql/",
        data=json.dumps(
            {
                "query": """
                query NotificationPreferences {
                  notificationPreferences {
                    inAppLikes
                    inAppComment
                    inAppNewFollowers
                    inAppMentionAndTags
                    interactionLikes
                    interactionComment
                    interactionPostInteraction
                    interactionNewFollower
                    circleLikes
                    circleAnchorPost
                    circleComment
                    circleFriendInteraction
                  }
                }
                """,
            }
        ),
        content_type="application/json",
    )

    content = json.loads(response.content)
    assert "errors" not in content
    preferences = content["data"]["notificationPreferences"]
    assert preferences["inAppLikes"] is True
    assert preferences["circleAnchorPost"] is True


@pytest.mark.django_db
def test_update_notification_preferences_accepts_bearer_token(authenticated_user):
    client = Client()
    client.defaults["HTTP_AUTHORIZATION"] = f'Bearer {authenticated_user["access_token"]}'

    response = client.post(
        "/graphql/",
        data=json.dumps(
            {
                "query": """
                mutation UpdateNotificationPreferences {
                  updateNotificationPreferences(
                    preferences: {
                      circleAnchorPost: false
                      circleComment: true
                      inAppLikes: true
                    }
                  ) {
                    inAppLikes
                    circleAnchorPost
                    circleComment
                  }
                }
                """,
            }
        ),
        content_type="application/json",
    )

    content = json.loads(response.content)
    assert "errors" not in content
    preferences = content["data"]["updateNotificationPreferences"]
    assert preferences["inAppLikes"] is True
    assert preferences["circleAnchorPost"] is False
    assert preferences["circleComment"] is True

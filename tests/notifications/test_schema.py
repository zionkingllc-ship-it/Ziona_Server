import json

import pytest
from django.test import Client


@pytest.mark.django_db
def test_notification_preferences_accepts_bearer_token(authenticated_user):
    client = Client()
    client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {authenticated_user['access_token']}"

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
    client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {authenticated_user['access_token']}"

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


@pytest.mark.django_db
def test_notifications_include_navigation_destination(authenticated_user, settings):
    from core.notifications.models import NotificationType
    from core.notifications.services import create_notification
    from core.posts.models import Post

    settings.APP_SHARE_BASE_URL = "https://share.ziona.test"
    user = authenticated_user["user"]
    post = Post.objects.create(user=user, post_type="text", caption="Destination post")
    create_notification(
        user_id=user.id,
        type_str=NotificationType.LIKE_POST,
        reference_id=post.id,
        reference_type="post",
        message="liked your post",
        respect_preferences=False,
        bypass_duplicate_check=True,
    )

    client = Client()
    client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {authenticated_user['access_token']}"
    response = client.post(
        "/graphql/",
        data=json.dumps(
            {
                "query": """
                query Notifications {
                  notifications(limit: 10) {
                    items {
                      referenceId
                      referenceType
                      deepLink
                      destination {
                        route
                        entityType
                        entityId
                        secondaryEntityId
                        deepLink
                      }
                    }
                  }
                }
                """,
            }
        ),
        content_type="application/json",
    )

    content = json.loads(response.content)
    assert response.status_code == 200, content
    assert "errors" not in content, content.get("errors")

    item = content["data"]["notifications"]["items"][0]
    assert item["referenceId"] == str(post.id)
    assert item["referenceType"] == "post"
    assert item["deepLink"] == f"https://share.ziona.test/post/{post.id}"
    assert item["destination"] == {
        "route": "post_detail",
        "entityType": "post",
        "entityId": str(post.id),
        "secondaryEntityId": None,
        "deepLink": f"https://share.ziona.test/post/{post.id}",
    }

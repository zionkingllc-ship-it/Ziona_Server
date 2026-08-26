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
def test_update_notification_preferences_muted_user_ids_replace_semantics(
    authenticated_user, create_user
):
    user = authenticated_user["user"]
    muted_a = create_user(email="muted-a@example.com", username="muteda")
    muted_b = create_user(email="muted-b@example.com", username="mutedb")

    client = Client()
    client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {authenticated_user['access_token']}"

    def update_muted(muted_ids: list[str] | None):
        if muted_ids is None:
            mutation = """
                mutation UpdateNotificationPreferences {
                  updateNotificationPreferences(preferences: { inAppLikes: false }) {
                    inAppLikes
                    mutedUserIds
                  }
                }
            """
            variables = {}
        else:
            mutation = """
                mutation UpdateNotificationPreferences($mutedUserIds: [ID!]) {
                  updateNotificationPreferences(
                    preferences: { mutedUserIds: $mutedUserIds }
                  ) {
                    inAppLikes
                    mutedUserIds
                  }
                }
            """
            variables = {"mutedUserIds": muted_ids}

        response = client.post(
            "/graphql/",
            data=json.dumps({"query": mutation, "variables": variables}),
            content_type="application/json",
        )
        content = json.loads(response.content)
        assert "errors" not in content, content.get("errors")
        return content["data"]["updateNotificationPreferences"]

    first = update_muted([str(muted_a.id), str(muted_b.id)])
    assert set(first["mutedUserIds"]) == {str(muted_a.id), str(muted_b.id)}

    omitted = update_muted(None)
    assert omitted["inAppLikes"] is False
    assert set(omitted["mutedUserIds"]) == {str(muted_a.id), str(muted_b.id)}

    replaced = update_muted([str(muted_a.id)])
    assert replaced["mutedUserIds"] == [str(muted_a.id)]

    from core.notifications.models import NotificationMutedUser

    assert list(
        NotificationMutedUser.objects.filter(user=user).values_list("muted_user_id", flat=True)
    ) == [muted_a.id]


@pytest.mark.django_db
def test_notifications_category_filter_and_user_viewer_state(authenticated_user, create_user):
    from core.follows.models import Follow
    from core.notifications.models import Notification, NotificationType

    viewer = authenticated_user["user"]
    followed_sender = create_user(email="followed@example.com", username="followed")
    follower_sender = create_user(email="follower@example.com", username="follower")

    Follow.objects.create(follower=viewer, following=followed_sender)
    Follow.objects.create(follower=follower_sender, following=viewer)
    Notification.objects.create(
        user=viewer,
        sender=followed_sender,
        notification_type=NotificationType.LIKE_POST,
        reference_id=followed_sender.id,
        reference_type="user",
        message="followed liked",
    )
    Notification.objects.create(
        user=viewer,
        sender=follower_sender,
        notification_type=NotificationType.NEW_CIRCLE_POST,
        reference_id=follower_sender.id,
        reference_type="circle_post",
        message="circle post",
    )

    client = Client()
    client.defaults["HTTP_AUTHORIZATION"] = f"Bearer {authenticated_user['access_token']}"
    response = client.post(
        "/graphql/",
        data=json.dumps(
            {
                "query": """
                query InteractionNotifications {
                  notifications(limit: 10, category: INTERACTIONS) {
                    items {
                      type
                      user {
                        id
                        username
                        viewerState {
                          isFollowing
                          isFollowedBy
                          isOwner
                        }
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
    assert "errors" not in content, content.get("errors")
    items = content["data"]["notifications"]["items"]
    assert [item["type"] for item in items] == ["like_post"]
    assert items[0]["user"]["username"] == "followed"
    assert items[0]["user"]["viewerState"] == {
        "isFollowing": True,
        "isFollowedBy": False,
        "isOwner": False,
    }


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


@pytest.mark.django_db
def test_new_follower_notification_links_to_sender_profile(authenticated_user, settings):
    from core.notifications.models import NotificationType
    from core.notifications.services import create_notification
    from core.users.models import User

    settings.APP_SHARE_BASE_URL = "https://share.ziona.test"
    target = authenticated_user["user"]
    sender = User.objects.create_user(
        email="new-follower@example.com",
        username="newfollower",
        password="TestPass123!",  # pragma: allowlist secret
        is_email_verified=True,
    )

    create_notification(
        user_id=target.id,
        type_str=NotificationType.NEW_FOLLOWER,
        reference_id=sender.id,
        reference_type="user",
        title="New Follower",
        message="newfollower started following you",
        sender_id=sender.id,
    )

    query = """
        query {
          notifications(limit: 10) {
            items {
              type
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
    """
    response = Client().post(
        "/graphql/",
        data=json.dumps({"query": query}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {authenticated_user['access_token']}",
    )
    content = response.json()

    assert "errors" not in content
    item = content["data"]["notifications"]["items"][0]
    assert item["type"] == "new_follower"
    assert item["referenceType"] == "user"
    assert item["referenceId"] == str(sender.id)
    assert item["deepLink"] == f"https://share.ziona.test/profile/{sender.id}"
    assert item["destination"] == {
        "route": "profile",
        "entityType": "user",
        "entityId": str(sender.id),
        "secondaryEntityId": None,
        "deepLink": f"https://share.ziona.test/profile/{sender.id}",
    }

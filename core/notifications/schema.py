from enum import Enum

import strawberry
from django.utils import timezone
from strawberry.types import Info

from core.notifications.models import Notification, NotificationPreference, NotificationStatus
from core.notifications.services import (
    create_admin_announcement,
    get_notifications,
    get_unread_count,
    mark_as_read,
    register_device_token,
    update_preferences,
)


@strawberry.type
class UserMiniType:
    """Minimal user representation returned inside NotificationItem.user.

    Keeps the notification payload small — the mobile app only needs identity
    data (id, username, avatar) to render the notification row avatar/name.
    """

    id: str
    username: str
    avatar_url: str = strawberry.field(name="avatarUrl", default="")


@strawberry.type
class SuccessResponse:
    success: bool
    error: str | None = None


@strawberry.enum
class NotificationTypeEnum(Enum):
    # Enum uses the actual choices from NotificationType (but uppercase is standard for GQL)
    REPLY_COMMENT = "reply_comment"
    REPLY_POST = "reply_post"
    LIKE_POST = "like_post"
    LIKE_COMMENT = "like_comment"
    NEW_ANCHOR = "new_anchor"
    MENTION = "mention"
    NEW_CIRCLE_POST = "new_circle_post"
    ADMIN_ANNOUNCEMENT = "admin_announcement"


def _default_notification_title(notification_type: str) -> str:
    titles = {
        NotificationTypeEnum.REPLY_COMMENT.value: "New Reply",
        NotificationTypeEnum.REPLY_POST.value: "New Reply",
        NotificationTypeEnum.LIKE_POST.value: "New Like",
        NotificationTypeEnum.LIKE_COMMENT.value: "New Like",
        NotificationTypeEnum.NEW_ANCHOR.value: "New Anchor",
        NotificationTypeEnum.MENTION.value: "New Mention",
        NotificationTypeEnum.NEW_CIRCLE_POST.value: "New Circle Post",
        NotificationTypeEnum.ADMIN_ANNOUNCEMENT.value: "Ziona Update",
    }
    return titles.get(notification_type, "Ziona App")


@strawberry.type
class NotificationItem:
    id: strawberry.ID
    type: str
    title: str
    message: str
    reference_id: strawberry.ID | None
    reference_type: str
    is_read: bool
    created_at: str

    @strawberry.field
    def user(self) -> "UserMiniType | None":
        """The user who triggered this notification (sender).

        Resolved from the pre-fetched sender FK — zero extra DB queries
        because get_notifications() uses select_related('sender').
        """
        sender = getattr(self._instance, "sender", None)
        if sender is None:
            return None
        return UserMiniType(
            id=str(sender.id),
            username=sender.username or "",
            avatar_url=getattr(sender, "avatar_url", None) or "",
        )

    @classmethod
    def from_instance(cls, instance: Notification):
        obj = cls(
            id=strawberry.ID(str(instance.id)),
            type=instance.notification_type,
            title=instance.title or _default_notification_title(instance.notification_type),
            message=instance.message,
            reference_id=strawberry.ID(str(instance.reference_id))
            if instance.reference_id
            else None,
            reference_type=instance.reference_type,
            is_read=instance.is_read,
            created_at=instance.created_at.isoformat(),
        )
        # Store the ORM instance so the user() resolver can read sender
        # without an additional DB round-trip.
        obj._instance = instance
        return obj


@strawberry.type
class NotificationConnection:
    items: list[NotificationItem]
    has_more: bool
    next_cursor: str | None


@strawberry.type
class NotificationPreferencesType:
    in_app_likes: bool
    in_app_comment: bool
    in_app_new_followers: bool
    in_app_mention_and_tags: bool
    interaction_likes: bool
    interaction_comment: bool
    interaction_post_interaction: bool
    interaction_new_follower: bool
    circle_likes: bool
    circle_anchor_post: bool
    circle_comment: bool
    circle_friend_interaction: bool

    @classmethod
    def from_instance(cls, instance: NotificationPreference):
        return cls(
            in_app_likes=instance.in_app_likes,
            in_app_comment=instance.in_app_comment,
            in_app_new_followers=instance.in_app_new_followers,
            in_app_mention_and_tags=instance.in_app_mention_and_tags,
            interaction_likes=instance.interaction_likes,
            interaction_comment=instance.interaction_comment,
            interaction_post_interaction=instance.interaction_post_interaction,
            interaction_new_follower=instance.interaction_new_follower,
            circle_likes=instance.circle_likes,
            circle_anchor_post=instance.circle_anchor_post,
            circle_comment=instance.circle_comment,
            circle_friend_interaction=instance.circle_friend_interaction,
        )


@strawberry.input
class PreferencesInput:
    in_app_likes: bool | None = None
    in_app_comment: bool | None = None
    in_app_new_followers: bool | None = None
    in_app_mention_and_tags: bool | None = None
    interaction_likes: bool | None = None
    interaction_comment: bool | None = None
    interaction_post_interaction: bool | None = None
    interaction_new_follower: bool | None = None
    circle_likes: bool | None = None
    circle_anchor_post: bool | None = None
    circle_comment: bool | None = None
    circle_friend_interaction: bool | None = None


@strawberry.type
class NotificationQueries:
    @strawberry.field
    def notifications(
        self, info: Info, limit: int = 20, cursor: str | None = None
    ) -> NotificationConnection:
        user = info.context.request.user
        if not user.is_authenticated:
            raise Exception("Authentication required")

        qs = get_notifications(user.id, limit=limit + 1, cursor=cursor)
        items = list(qs)

        has_more = len(items) > limit
        if has_more:
            items = items[:limit]

        next_cursor = items[-1].created_at.isoformat() if items else None

        return NotificationConnection(
            items=[NotificationItem.from_instance(n) for n in items],
            has_more=has_more,
            next_cursor=next_cursor,
        )

    @strawberry.field
    def unread_notification_count(self, info: Info) -> int:
        user = info.context.request.user
        if not user.is_authenticated:
            raise Exception("Authentication required")
        return get_unread_count(user.id)

    @strawberry.field
    def notification_preferences(self, info: Info) -> NotificationPreferencesType:
        user = info.context.request.user
        if not user.is_authenticated:
            raise Exception("Authentication required")
        pref, _ = NotificationPreference.objects.get_or_create(user_id=user.id)
        return NotificationPreferencesType.from_instance(pref)


@strawberry.type
class NotificationMutations:
    @strawberry.mutation
    def mark_notification_as_read(
        self, info: Info, notification_id: strawberry.ID
    ) -> SuccessResponse:
        user = info.context.request.user
        if not user.is_authenticated:
            raise Exception("Authentication required")
        try:
            import uuid

            mark_as_read(uuid.UUID(str(notification_id)), user.id)
            return SuccessResponse(success=True)
        except Exception as e:
            return SuccessResponse(success=False, error=str(e))

    @strawberry.mutation
    def mark_all_notifications_as_read(self, info: Info) -> SuccessResponse:
        user = info.context.request.user
        if not user.is_authenticated:
            raise Exception("Authentication required")
        Notification.objects.filter(
            user_id=user.id, is_read=False, status=NotificationStatus.ACTIVE
        ).update(is_read=True, updated_at=timezone.now())
        return SuccessResponse(success=True)

    @strawberry.mutation
    def delete_notification(self, info: Info, notification_id: strawberry.ID) -> SuccessResponse:
        user = info.context.request.user
        if not user.is_authenticated:
            raise Exception("Authentication required")
        try:
            import uuid

            notif = Notification.objects.get(id=uuid.UUID(str(notification_id)), user_id=user.id)
            notif.status = NotificationStatus.DELETED
            notif.save(update_fields=["status", "updated_at"])
            return SuccessResponse(success=True)
        except Exception as e:
            return SuccessResponse(success=False, error=str(e))

    @strawberry.mutation
    def update_notification_preferences(
        self, info: Info, preferences: PreferencesInput
    ) -> NotificationPreferencesType:
        user = info.context.request.user
        if not user.is_authenticated:
            raise Exception("Authentication required")

        pref_dict = {}
        for field in (
            "in_app_likes",
            "in_app_comment",
            "in_app_new_followers",
            "in_app_mention_and_tags",
            "interaction_likes",
            "interaction_comment",
            "interaction_post_interaction",
            "interaction_new_follower",
            "circle_likes",
            "circle_anchor_post",
            "circle_comment",
            "circle_friend_interaction",
        ):
            value = getattr(preferences, field)
            if value is not None:
                pref_dict[field] = value

        updated_pref = update_preferences(user.id, pref_dict)
        return NotificationPreferencesType.from_instance(updated_pref)

    @strawberry.mutation
    def register_device_token(self, info: Info, token: str, platform: str) -> SuccessResponse:
        user = info.context.request.user
        if not user.is_authenticated:
            raise Exception("Authentication required")
        register_device_token(user.id, token, platform)
        # We can pass the message or just return True
        return SuccessResponse(success=True)

    @strawberry.mutation
    def send_admin_announcement(
        self, info: Info, message: str, target_users: list[strawberry.ID] | None = None
    ) -> SuccessResponse:
        user = info.context.request.user
        if not user.is_authenticated or not user.is_staff:
            raise Exception("UNAUTHORIZED_ADMIN_ACTION")

        targets = [int(tid) for tid in target_users] if target_users else None
        create_admin_announcement(admin_id=user.id, message=message, target_users=targets)
        return SuccessResponse(success=True)

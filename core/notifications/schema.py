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


@strawberry.type
class NotificationItem:
    id: strawberry.ID
    type: str  # using string since Strawberry enums can be tricky to map directly if values don't match
    message: str
    reference_id: strawberry.ID | None
    reference_type: str
    is_read: bool
    created_at: str

    @classmethod
    def from_instance(cls, instance: Notification):
        return cls(
            id=strawberry.ID(str(instance.id)),
            type=instance.notification_type,
            message=instance.message,
            reference_id=strawberry.ID(str(instance.reference_id))
            if instance.reference_id
            else None,
            reference_type=instance.reference_type,
            is_read=instance.is_read,
            created_at=instance.created_at.isoformat(),
        )


@strawberry.type
class NotificationConnection:
    items: list[NotificationItem]
    has_more: bool
    next_cursor: str | None


@strawberry.type
class NotificationPreferencesType:
    anchor_notifications: bool
    reply_notifications: bool
    like_notifications: bool
    circle_activity_notifications: bool
    admin_announcements: bool

    @classmethod
    def from_instance(cls, instance: NotificationPreference):
        return cls(
            anchor_notifications=instance.anchor_notifications,
            reply_notifications=instance.reply_notifications,
            like_notifications=instance.like_notifications,
            circle_activity_notifications=instance.circle_activity_notifications,
            admin_announcements=instance.admin_announcements,
        )


@strawberry.input
class PreferencesInput:
    anchor_notifications: bool | None = None
    reply_notifications: bool | None = None
    like_notifications: bool | None = None
    circle_activity_notifications: bool | None = None
    admin_announcements: bool | None = None


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
        if preferences.anchor_notifications is not None:
            pref_dict["anchor_notifications"] = preferences.anchor_notifications
        if preferences.reply_notifications is not None:
            pref_dict["reply_notifications"] = preferences.reply_notifications
        if preferences.like_notifications is not None:
            pref_dict["like_notifications"] = preferences.like_notifications
        if preferences.circle_activity_notifications is not None:
            pref_dict["circle_activity_notifications"] = preferences.circle_activity_notifications
        if preferences.admin_announcements is not None:
            pref_dict["admin_announcements"] = preferences.admin_announcements

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

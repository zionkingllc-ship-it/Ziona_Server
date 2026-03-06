"""
Notification service — business logic for in-app notifications.

Handles creating, fetching, reading, and deleting notifications
with cursor-based pagination.
"""

import logging

from core.notifications.models import Notification, NotificationType
from core.shared.exceptions import ErrorCode, ZionaError

logger = logging.getLogger("core.notifications")


class NotificationError(ZionaError):
    """Raised when notification operations fail."""

    pass


class NotificationService:
    """Service handling notification operations."""

    @staticmethod
    def get_notifications(
        user_id: str,
        filter_type: str = "all",
        limit: int = 20,
        cursor: str | None = None,
    ) -> dict:
        """Fetch notifications with filtering and cursor pagination.

        Args:
            user_id: ID of the user requesting notifications.
            filter_type: "all", "follows", "mentions", "replies", "circles".
            limit: Maximum number of notifications to return.
            cursor: Pagination cursor (ISO 8601 string of created_at).

        Returns:
            Dict containing mapping of list of notifications and next cursor.
        """
        if limit <= 0 or limit > 50:
            limit = 20

        queryset = Notification.objects.select_related("actor").filter(recipient_id=user_id)

        if filter_type != "all":
            if filter_type == "follows":
                queryset = queryset.filter(type=NotificationType.FOLLOW)
            elif filter_type == "mentions":
                queryset = queryset.filter(type=NotificationType.MENTION)
            elif filter_type == "replies":
                queryset = queryset.filter(type=NotificationType.REPLY)
            elif filter_type == "circles":
                queryset = queryset.filter(type=NotificationType.CIRCLE_POST)

        if cursor:
            queryset = queryset.filter(created_at__lt=cursor)

        notifications = list(queryset[: limit + 1])

        has_more = len(notifications) > limit
        if has_more:
            notifications.pop()

        next_cursor = notifications[-1].created_at.isoformat() if notifications else None

        results = []
        for n in notifications:
            results.append(
                {
                    "id": str(n.id),
                    "type": n.type,
                    "message": n.message,
                    "is_read": n.is_read,
                    "created_at": n.created_at,
                    "actor": {
                        "username": n.actor.username,
                        "avatar_url": n.actor.avatar_url,
                    },
                }
            )

        return {
            "notifications": results,
            "next_cursor": next_cursor,
            "has_more": has_more,
        }

    @staticmethod
    def delete_notification(notification_id: str, user_id: str) -> bool:
        """Delete a single notification ensuring ownership.

        Args:
            notification_id: The ID of the notification.
            user_id: The requesting user's ID.

        Returns:
            True if deleted.

        Raises:
            NotificationError: If notification not found or doesn't belong to user.
        """
        try:
            notification = Notification.objects.get(id=notification_id)
        except Notification.DoesNotExist:
            raise NotificationError(
                "Notification not found",
                code=ErrorCode.NOT_FOUND,
            ) from None

        if str(notification.recipient_id) != str(user_id):
            raise NotificationError(
                "Cannot delete another user's notification",
                code=ErrorCode.UNAUTHORIZED,
            )

        notification.delete()
        return True

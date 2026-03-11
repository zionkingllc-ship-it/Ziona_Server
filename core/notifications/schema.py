"""
Notification GraphQL schemas and resolvers.
"""


import strawberry
from strawberry.types import Info

from core.notifications.services import NotificationService
from core.users.schema import _get_authenticated_user_id


@strawberry.type
class NotificationActor:
    username: str
    avatarUrl: str


@strawberry.type
class NotificationDTO:
    """
    Base Notification Entity representing a single interaction alert.

    Contains resolved `actor` sub-objects.
    """

    id: strawberry.ID
    type: str
    message: str
    isRead: bool
    createdAt: str
    actor: NotificationActor


@strawberry.type
class NotificationResponseListDTO:
    """
    Paginated Notification array response.

    **Authentication:** Required
    **Related operations:** notifications
    """

    notifications: list[NotificationDTO] = strawberry.field(
        description="Requested chunk array directly natively"
    )
    nextCursor: str | None = strawberry.field(default=None, description="Passed backwards safely")
    hasMore: bool = strawberry.field(
        default=False, description="Scrolling bounds limit tracking flag"
    )


@strawberry.type
class DeleteNotificationResponse:
    """
    Response confirming deletion of a notification object.

    **Authentication:** Required
    **Related operations:** delete_notification
    """

    success: bool = strawberry.field(description="Confirms deletion execution")


@strawberry.type
class NotificationQueries:
    @strawberry.field(
        description="Retrieve descending paginated array of authenticated user's notifications natively."
    )
    def notifications(
        self,
        info: Info,
        filter: str = "all",
        cursor: str | None = None,
        limit: int = 20,
    ) -> NotificationResponseListDTO:
        """
        Get chronologically sorted notifications securely tracking real-time activity metrics.

        Dynamically filters if standard 'unread' flags apply.

        **Authentication:** Required
        **Parameters:**
        - filter (String, optional) - Set literal filtering natively bounds
        - cursor (String, optional) - Token Hash natively bounding
        - limit (Int, optional) - Volume Cap
        **Returns:** NotificationResponseListDTO mapping exactly natively bounds safely
        **Errors:** Returns natively bound empty struct if UNAUTHENTICATED gracefully.
        """
        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return NotificationResponseListDTO(notifications=[], hasMore=False)

        result = NotificationService.get_notifications(
            user_id=user_id,
            filter_type=filter,
            limit=limit,
            cursor=cursor,
        )

        dtos = []
        for n in result["notifications"]:
            dtos.append(
                NotificationDTO(
                    id=n["id"],
                    type=n["type"],
                    message=n["message"],
                    isRead=n["is_read"],
                    createdAt=n["created_at"].isoformat()
                    if hasattr(n["created_at"], "isoformat")
                    else str(n["created_at"]),
                    actor=NotificationActor(
                        username=n["actor"]["username"],
                        avatarUrl=n["actor"]["avatar_url"],
                    ),
                )
            )

        return NotificationResponseListDTO(
            notifications=dtos,
            nextCursor=result["next_cursor"],
            hasMore=result["has_more"],
        )


@strawberry.type
class NotificationMutations:
    @strawberry.mutation(
        description="Soft delete a notification entity explicitly targeting a UUID."
    )
    def delete_notification(self, info: Info, id: strawberry.ID) -> DeleteNotificationResponse:
        """
        Permanently remove a notification alert from a user's local inbox natively safely.

        **Authentication:** Required
        **Parameters:**
        - id (String, required) - Valid UUID mapping explicitly
        **Returns:** DeleteNotificationResponse tracking execution state safely
        **Errors:** UNAUTHENTICATED safely bounded.
        """
        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return DeleteNotificationResponse(success=False)
        success = NotificationService.delete_notification(str(id), user_id)
        return DeleteNotificationResponse(success=success)

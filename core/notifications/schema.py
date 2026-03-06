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
    id: strawberry.ID
    type: str
    message: str
    isRead: bool
    createdAt: str
    actor: NotificationActor


@strawberry.type
class NotificationResponseListDTO:
    notifications: list[NotificationDTO]
    nextCursor: str | None = None
    hasMore: bool = False


@strawberry.type
class DeleteNotificationResponse:
    success: bool


@strawberry.type
class NotificationQueries:
    @strawberry.field
    def notifications(
        self,
        info: Info,
        filter: str = "all",
        cursor: str | None = None,
        limit: int = 20,
    ) -> NotificationResponseListDTO:
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
    @strawberry.mutation
    def delete_notification(self, info: Info, id: strawberry.ID) -> DeleteNotificationResponse:
        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return DeleteNotificationResponse(success=False)
        success = NotificationService.delete_notification(str(id), user_id)
        return DeleteNotificationResponse(success=success)

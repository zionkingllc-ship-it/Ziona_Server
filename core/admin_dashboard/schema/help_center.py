"""Help center articles + user support conversations (user-facing).

Split from the former core/admin_dashboard/schema.py (no contract change).
"""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from core.shared.request_utils import get_client_ip
from core.shared.types import ErrorType


@strawberry.type
class HelpArticleType:
    """A public help-center article."""

    id: str
    slug: str
    title: str
    summary: str
    content: str
    category_slug: str = strawberry.field(name="categorySlug")
    category_title: str = strawberry.field(name="categoryTitle")


@strawberry.type
class HelpCategoryType:
    """A public help-center category."""

    id: str
    slug: str
    title: str
    description: str
    article_count: int = strawberry.field(name="articleCount")
    articles: list[HelpArticleType]


@strawberry.type
class HelpConversationMessageType:
    """A single viewer/admin message inside a support thread."""

    id: str
    message: str
    sent_at: str = strawberry.field(name="sentAt")
    sender_type: str = strawberry.field(name="senderType")
    sender_name: str = strawberry.field(name="senderName")


@strawberry.type
class HelpConversationType:
    """Mobile-facing help/support conversation thread."""

    id: str
    topic: str
    status: str
    created_at: str = strawberry.field(name="createdAt")
    updated_at: str = strawberry.field(name="updatedAt")
    last_message_at: str | None = strawberry.field(name="lastMessageAt", default=None)
    replied_at: str | None = strawberry.field(name="repliedAt", default=None)
    latest_message: HelpConversationMessageType | None = strawberry.field(
        name="latestMessage",
        default=None,
    )
    messages: list[HelpConversationMessageType] = strawberry.field(default_factory=list)


@strawberry.type
class HelpConversationMessagesPageType:
    """Cursor page used by mobile while polling an open support thread."""

    messages: list[HelpConversationMessageType]
    next_cursor: str | None = strawberry.field(name="nextCursor", default=None)
    has_more: bool = strawberry.field(name="hasMore", default=False)


@strawberry.type
class HelpConversationPayload:
    """Response for support-thread mutations."""

    success: bool
    contact: HelpConversationType | None = None
    error: ErrorType | None = None


def _map_help_article(category, article) -> HelpArticleType:
    """Convert static help content into GraphQL shape."""
    return HelpArticleType(
        id=article.slug,
        slug=article.slug,
        title=article.title,
        summary=article.summary,
        content=article.content,
        category_slug=category.slug,
        category_title=category.title,
    )


def _map_help_category(category) -> HelpCategoryType:
    """Convert a help category into GraphQL shape."""
    return HelpCategoryType(
        id=category.slug,
        slug=category.slug,
        title=category.title,
        description=category.description,
        article_count=len(category.articles),
        articles=[_map_help_article(category, article) for article in category.articles],
    )


def _map_help_article_record(article) -> HelpArticleType:
    """Lookup the parent category for a help article and map it."""
    from core.admin_dashboard.help_center import get_help_article

    record = get_help_article(article.slug)
    if not record:
        raise ValueError(f"Help article '{article.slug}' is missing its category mapping.")
    category, article = record
    return _map_help_article(category, article)


def _map_help_conversation(conversation: dict) -> HelpConversationType:
    """Convert service-layer help conversation dict to GraphQL type."""
    return HelpConversationType(
        id=conversation["id"],
        topic=conversation.get("topic", ""),
        status=conversation["status"],
        created_at=conversation["created_at"],
        updated_at=conversation["updated_at"],
        last_message_at=conversation.get("last_message_at"),
        replied_at=conversation.get("replied_at"),
        latest_message=(
            _map_help_conversation_message(conversation["latest_message"])
            if conversation.get("latest_message")
            else None
        ),
        messages=[
            _map_help_conversation_message(message) for message in conversation.get("messages", [])
        ],
    )


def _map_help_conversation_message(message: dict) -> HelpConversationMessageType:
    return HelpConversationMessageType(
        id=message["id"],
        message=message["message"],
        sent_at=message["sent_at"],
        sender_type=message["sender_type"],
        sender_name=message["sender_name"],
    )


@strawberry.type
class HelpCenterAdminQueries:
    @strawberry.field(name="helpCategories", description="List public help-center categories.")
    def help_categories(
        self,
        info: Info,
        search: str = "",
    ) -> list[HelpCategoryType]:
        from core.admin_dashboard.help_center import list_help_categories

        categories = list_help_categories(search=search)
        return [_map_help_category(category) for category in categories]

    @strawberry.field(name="helpArticles", description="List public help-center articles.")
    def help_articles(
        self,
        info: Info,
        category_slug: str | None = None,
        search: str = "",
    ) -> list[HelpArticleType]:
        from core.admin_dashboard.help_center import list_help_articles

        return [
            _map_help_article_record(article_record)
            for article_record in list_help_articles(category_slug=category_slug, search=search)
        ]

    @strawberry.field(
        name="myHelpConversations",
        description="List support conversations for the authenticated user.",
    )
    def my_help_conversations(
        self,
        info: Info,
        status: str = "",
    ) -> list[HelpConversationType]:
        from core.admin_dashboard.contact_services import ContactService
        from core.shared.exceptions import AuthenticationError
        from core.users.models import User
        from core.users.schema import _get_authenticated_user_id

        user_id = _get_authenticated_user_id(info)
        if not user_id:
            raise AuthenticationError("Authentication required", "UNAUTHENTICATED")

        user = User.objects.filter(id=user_id, deleted_at__isnull=True).first()
        if not user:
            raise AuthenticationError("Authentication required", "UNAUTHENTICATED")

        return [
            _map_help_conversation(conversation)
            for conversation in ContactService.list_help_conversations(
                user=user,
                status_filter=status,
            )
        ]

    @strawberry.field(
        name="helpConversation",
        description="Return one support conversation owned by the authenticated user.",
    )
    def help_conversation(self, info: Info, contact_id: str) -> HelpConversationType | None:
        from core.admin_dashboard.contact_services import ContactService
        from core.shared.exceptions import AuthenticationError
        from core.users.models import User
        from core.users.schema import _get_authenticated_user_id

        user_id = _get_authenticated_user_id(info)
        user = User.objects.filter(id=user_id).first() if user_id else None
        if not user:
            raise AuthenticationError("Authentication required", "UNAUTHENTICATED")
        return _map_help_conversation(ContactService.get_help_conversation(contact_id, user=user))

    @strawberry.field(
        name="helpConversationMessages",
        description="Poll new support messages after the last received message cursor.",
    )
    def help_conversation_messages(
        self,
        info: Info,
        contact_id: str,
        after: str = "",
        first: int = 50,
    ) -> HelpConversationMessagesPageType:
        from core.admin_dashboard.contact_services import ContactService
        from core.shared.exceptions import AuthenticationError
        from core.users.models import User
        from core.users.schema import _get_authenticated_user_id

        user_id = _get_authenticated_user_id(info)
        user = User.objects.filter(id=user_id).first() if user_id else None
        if not user:
            raise AuthenticationError("Authentication required", "UNAUTHENTICATED")

        result = ContactService.list_help_conversation_messages(
            contact_id,
            user=user,
            after=after,
            first=first,
        )
        return HelpConversationMessagesPageType(
            messages=[_map_help_conversation_message(item) for item in result["messages"]],
            next_cursor=result["next_cursor"],
            has_more=result["has_more"],
        )


@strawberry.type
class HelpCenterAdminMutations:
    @strawberry.mutation(
        name="submitHelpMessage",
        description="Authenticated in-app help/support submission.",
    )
    def submit_help_message(
        self,
        info: Info,
        message: str,
        category_slug: str | None = None,
        name: str | None = None,
        email: str | None = None,
    ) -> HelpConversationPayload:
        from core.admin_dashboard.contact_services import ContactService
        from core.shared.exceptions import AdminError
        from core.users.models import User
        from core.users.schema import _get_authenticated_user_id

        try:
            user_id = _get_authenticated_user_id(info)
            user = (
                User.objects.filter(id=user_id, deleted_at__isnull=True).first()
                if user_id
                else None
            )
            if not user:
                return HelpConversationPayload(
                    success=False,
                    error=ErrorType(
                        code="UNAUTHENTICATED",
                        message="Authentication is required for in-app support.",
                    ),
                )
            result = ContactService.submit_help_message(
                message=message,
                category_slug=category_slug or "",
                ip_address=get_client_ip(info.context.request, default=""),
                user=user,
                name=name or "",
                email=email or "",
            )
            return HelpConversationPayload(
                success=True,
                contact=_map_help_conversation(result["contact"]),
            )
        except AdminError as e:
            return HelpConversationPayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message, details=e.extensions or None),
            )

    @strawberry.mutation(
        name="sendHelpMessage",
        description="Append an idempotent message to an existing in-app support thread.",
    )
    def send_help_message(
        self,
        info: Info,
        contact_id: str,
        message: str,
        client_message_id: str,
    ) -> HelpConversationPayload:
        from core.admin_dashboard.contact_services import ContactService
        from core.shared.exceptions import AdminError
        from core.users.models import User
        from core.users.schema import _get_authenticated_user_id

        try:
            user_id = _get_authenticated_user_id(info)
            user = User.objects.filter(id=user_id).first() if user_id else None
            result = ContactService.send_help_message(
                contact_id,
                message=message,
                client_message_id=client_message_id,
                user=user,
            )
            return HelpConversationPayload(
                success=True,
                contact=_map_help_conversation(result["contact"]),
            )
        except AdminError as e:
            return HelpConversationPayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message, details=e.extensions or None),
            )

    @strawberry.mutation(
        name="resolveHelpConversation",
        description="Mark a support conversation as resolved for the authenticated user.",
    )
    def resolve_help_conversation(
        self,
        info: Info,
        contact_id: str,
    ) -> HelpConversationPayload:
        from core.admin_dashboard.contact_services import ContactService
        from core.shared.exceptions import AdminError
        from core.users.models import User
        from core.users.schema import _get_authenticated_user_id

        try:
            user_id = _get_authenticated_user_id(info)
            user = (
                User.objects.filter(id=user_id, deleted_at__isnull=True).first()
                if user_id
                else None
            )
            result = ContactService.resolve_help_conversation(
                contact_id=contact_id,
                user=user,
                email=user.email if user else "",
            )
            return HelpConversationPayload(
                success=True,
                contact=_map_help_conversation(result["contact"]),
            )
        except AdminError as e:
            return HelpConversationPayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )

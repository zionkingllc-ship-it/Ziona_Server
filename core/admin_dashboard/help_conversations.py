"""Help-center conversation operations (user support chat).

Split from core/admin_dashboard/contact_services.py; mixed into ContactService
so its public method surface is unchanged (no behavior change).
"""

import logging

from django.db import transaction
from django.db.models import Q

from core.admin_dashboard.help_center import get_help_category
from core.shared.exceptions import AdminError, ErrorCode

logger = logging.getLogger("core.admin_dashboard")


class HelpConversationOps:
    """Help-conversation methods mixed into ContactService."""

    @staticmethod
    def submit_help_message(
        *,
        message: str,
        category_slug: str = "",
        ip_address: str = "",
        user=None,
        name: str = "",
        email: str = "",
    ) -> dict:
        """Create a new authenticated in-app support thread."""
        from core.admin_dashboard.models import ContactMessage

        if not user:
            raise AdminError(
                message="Authentication is required for in-app support.",
                code=ErrorCode.UNAUTHORIZED,
            )

        _check_help_rate_limit(
            action="create",
            user_id=str(user.id),
            max_requests=5,
            window_seconds=3600,
        )

        category = None
        if category_slug:
            category = get_help_category(category_slug)
            if not category:
                raise AdminError(
                    message="Help category not found.",
                    code=ErrorCode.CATEGORY_NOT_FOUND,
                )

        resolved_name = user.full_name or user.username or user.email.split("@")[0]
        resolved_email = user.email

        from core.admin_dashboard.contact_services import ContactService

        result = ContactService.submit_message(
            name=resolved_name,
            email=resolved_email,
            message=message,
            ip_address=ip_address,
            source="mobile_help",
            brand="ZIONA",
            topic=category.title if category else "",
            requester_user=user,
        )
        contact = (
            ContactMessage.objects.select_related("requester_user")
            .prefetch_related("conversation_messages__sender_user")
            .get(id=result["contact_id"])
        )
        result["contact"] = _contact_to_help_thread(contact)
        return result

    @staticmethod
    def list_help_conversations(
        *, user=None, email: str = "", status_filter: str = ""
    ) -> list[dict]:
        """Return authenticated viewer support threads with admin replies."""
        from core.admin_dashboard.models import ContactMessage

        if not user:
            return []

        filters = Q(source__in=["mobile_help", "mobile_app"])
        filters &= Q(requester_user=user)

        qs = (
            ContactMessage.objects.select_related("requester_user")
            .filter(filters)
            .prefetch_related("conversation_messages__sender_user")
        )
        if status_filter:
            qs = qs.filter(status=status_filter)

        return [
            _contact_to_help_thread(contact)
            for contact in qs.order_by("-last_message_at", "-created_at")
        ]

    @staticmethod
    def get_help_conversation(contact_id: str, *, user) -> dict:
        """Return one viewer-owned in-app support conversation."""
        contact = _get_owned_help_contact(contact_id, user=user)
        return _contact_to_help_thread(contact)

    @staticmethod
    def list_help_conversation_messages(
        contact_id: str,
        *,
        user,
        after: str = "",
        first: int = 50,
    ) -> dict:
        """Return cursor-paginated support messages for polling clients."""
        contact = _get_owned_help_contact(contact_id, user=user, prefetch=False)
        first = max(1, min(first, 100))
        qs = contact.conversation_messages.select_related("sender_user").order_by(
            "created_at", "id"
        )

        if after:
            cursor_message = qs.filter(id=after).first()
            if not cursor_message:
                raise AdminError(
                    message="Invalid support message cursor.",
                    code=ErrorCode.INVALID_PAGINATION_CURSOR,
                )
            qs = qs.filter(
                Q(created_at__gt=cursor_message.created_at)
                | Q(created_at=cursor_message.created_at, id__gt=cursor_message.id)
            )

        messages = list(qs[: first + 1])
        has_more = len(messages) > first
        messages = messages[:first]
        return {
            "messages": [_conversation_message_to_dict(message) for message in messages],
            "next_cursor": str(messages[-1].id) if messages else (after or None),
            "has_more": has_more,
        }

    @staticmethod
    @transaction.atomic
    def send_help_message(
        contact_id: str,
        *,
        message: str,
        client_message_id: str,
        user,
    ) -> dict:
        """Append an idempotent user message to an existing support thread."""
        from core.admin_dashboard.models import (
            ContactConversationMessage,
            ContactMessage,
            ContactSenderType,
            ContactStatus,
        )

        if not user:
            raise AdminError("Authentication required.", ErrorCode.UNAUTHORIZED)

        cleaned_message = (message or "").strip()
        cleaned_client_id = (client_message_id or "").strip()
        if not cleaned_message:
            raise AdminError("Message is required.", ErrorCode.VALIDATION_ERROR)
        if not cleaned_client_id or len(cleaned_client_id) > 100:
            raise AdminError(
                "A clientMessageId of at most 100 characters is required.",
                ErrorCode.VALIDATION_ERROR,
            )

        contact = (
            ContactMessage.objects.select_for_update()
            .filter(
                id=contact_id,
                requester_user=user,
                source__in=["mobile_help", "mobile_app"],
            )
            .first()
        )
        if not contact:
            raise AdminError("Contact message not found.", ErrorCode.CONTACT_NOT_FOUND)

        existing = ContactConversationMessage.objects.filter(
            contact=contact,
            client_message_id=cleaned_client_id,
        ).first()
        if existing:
            contact = _reload_help_contact(contact.id)
            return {"success": True, "contact": _contact_to_help_thread(contact)}

        _check_help_rate_limit("append_minute", str(user.id), 30, 60)
        _check_help_rate_limit("append_day", str(user.id), 500, 86400)

        appended = ContactConversationMessage.objects.create(
            contact=contact,
            sender_type=ContactSenderType.USER,
            sender_user=user,
            message=cleaned_message,
            client_message_id=cleaned_client_id,
        )
        if contact.status == ContactStatus.RESOLVED:
            contact.status = ContactStatus.PENDING
        contact.last_message_at = appended.created_at
        contact.save(update_fields=["status", "last_message_at", "updated_at"])
        contact = _reload_help_contact(contact.id)
        return {"success": True, "contact": _contact_to_help_thread(contact)}

    @staticmethod
    @transaction.atomic
    def resolve_help_conversation(contact_id: str, *, user=None, email: str = "") -> dict:
        """Mark a support conversation as resolved for the requesting viewer."""
        from core.admin_dashboard.models import ContactMessage, ContactStatus

        contact = ContactMessage.objects.select_for_update().filter(id=contact_id).first()
        if not contact:
            raise AdminError(
                message="Contact message not found.",
                code=ErrorCode.CONTACT_NOT_FOUND,
            )

        belongs_to_viewer = bool(user and contact.requester_user_id == user.id)

        if not belongs_to_viewer:
            raise AdminError(
                message="You do not have access to this support conversation.",
                code=ErrorCode.PERMISSION_DENIED,
            )

        contact.status = ContactStatus.RESOLVED
        contact.save(update_fields=["status"])
        return {"success": True, "contact": _contact_to_help_thread(contact)}


def _contact_to_help_thread(contact) -> dict:
    """Convert a contact message and replies into a mobile-friendly conversation."""
    messages = [
        _conversation_message_to_dict(message) for message in contact.conversation_messages.all()
    ]

    return {
        "id": str(contact.id),
        "topic": contact.topic,
        "status": contact.status,
        "created_at": contact.created_at.isoformat(),
        "updated_at": contact.updated_at.isoformat(),
        "last_message_at": (
            contact.last_message_at.isoformat() if contact.last_message_at else None
        ),
        "replied_at": contact.replied_at.isoformat() if contact.replied_at else None,
        "messages": messages,
        "latest_message": messages[-1] if messages else None,
    }


def _conversation_message_to_dict(message) -> dict:
    sender_name = "Ziona Support"
    if message.sender_type == "USER":
        sender_name = (
            message.sender_user.full_name or message.sender_user.username
            if message.sender_user
            else message.contact.name
        )
    elif message.sender_user:
        sender_name = message.sender_user.full_name or message.sender_user.username

    return {
        "id": str(message.id),
        "message": message.message,
        "sent_at": message.created_at.isoformat(),
        "sender_type": message.sender_type,
        "sender_name": sender_name,
    }


def _reload_help_contact(contact_id):
    from core.admin_dashboard.models import ContactMessage

    return (
        ContactMessage.objects.select_related("requester_user")
        .prefetch_related("conversation_messages__sender_user")
        .get(id=contact_id)
    )


def _get_owned_help_contact(contact_id: str, *, user, prefetch: bool = True):
    from core.admin_dashboard.models import ContactMessage

    if not user:
        raise AdminError("Authentication required.", ErrorCode.UNAUTHORIZED)
    qs = ContactMessage.objects.select_related("requester_user").filter(
        id=contact_id,
        requester_user=user,
        source__in=["mobile_help", "mobile_app"],
    )
    if prefetch:
        qs = qs.prefetch_related("conversation_messages__sender_user")
    contact = qs.first()
    if not contact:
        raise AdminError("Contact message not found.", ErrorCode.CONTACT_NOT_FOUND)
    return contact


def _check_help_rate_limit(
    action: str,
    user_id: str,
    max_requests: int,
    window_seconds: int,
) -> None:
    from core.shared.redis_lua import LuaLimiter

    limited, retry_after = LuaLimiter.check_rate_limit(
        f"ratelimit:help:{action}:{user_id}",
        max_requests,
        window_seconds,
    )
    if limited:
        raise AdminError(
            message=f"Too many support messages. Try again in {retry_after} seconds.",
            code=ErrorCode.RATE_LIMIT_EXCEEDED,
            extensions={"retryAfter": retry_after},
        )

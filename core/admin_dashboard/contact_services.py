"""
Contact/Support service — ticket management and admin replies.

Replies send email via Django's send_mail (Ensend backend).
"""

import logging
from datetime import datetime, timezone

from django.conf import settings
from django.db import transaction
from django.db.models import Q

from core.admin_dashboard.help_center import get_help_category
from core.admin_dashboard.permissions import log_admin_action
from core.shared.exceptions import AdminError, ErrorCode

logger = logging.getLogger("core.admin_dashboard")


class ContactService:
    """Service for contact message listing, replying, and status management."""

    @staticmethod
    def list_contacts(
        status_filter: str = "",
        search: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """List contact messages with filtering and pagination.

        Uses prefetch_related for replies to avoid N+1.
        """
        from core.admin_dashboard.models import ContactMessage

        page_size = min(page_size, 50)
        offset = (page - 1) * page_size

        qs = (
            ContactMessage.objects.select_related("requester_user")
            .prefetch_related("conversation_messages__sender_user")
            .order_by("-last_message_at", "-created_at")
        )

        if status_filter:
            qs = qs.filter(status=status_filter)

        if search:
            from django.db.models import Q

            qs = qs.filter(
                Q(name__icontains=search)
                | Q(email__icontains=search)
                | Q(message__icontains=search)
                | Q(topic__icontains=search)
                | Q(source__icontains=search)
                | Q(brand__icontains=search)
            )

        total_count = qs.count()
        contacts = list(qs[offset : offset + page_size])

        from django.db.models import Count
        from django.db.models import Q as DQ

        summary = ContactMessage.objects.aggregate(
            total=Count("id"),
            pending=Count("id", filter=DQ(status="pending")),
            in_progress=Count("id", filter=DQ(status="in_progress")),
            resolved=Count("id", filter=DQ(status="resolved")),
        )

        return {
            "contacts": [_contact_to_dict(c) for c in contacts],
            "total_count": total_count,
            "page": page,
            "page_size": page_size,
            "total_pages": max(1, (total_count + page_size - 1) // page_size),
            "summary": summary,
        }

    @staticmethod
    @transaction.atomic
    def reply_to_contact(
        contact_id: str,
        message: str,
        admin_user=None,
        ip_address: str = "",
    ) -> dict:
        """Reply to a contact message. Sends email and updates status.

        Appends an ADMIN conversation message and updates the ticket status.
        Landing contacts receive an email; in-app support replies use push only.
        """
        from core.admin_dashboard.models import (
            ContactConversationMessage,
            ContactMessage,
            ContactSenderType,
            ContactStatus,
        )

        contact = ContactMessage.objects.select_for_update().filter(id=contact_id).first()

        if not contact:
            raise AdminError(
                message="Contact message not found.",
                code=ErrorCode.CONTACT_NOT_FOUND,
            )

        reply = ContactConversationMessage.objects.create(
            contact=contact,
            sender_type=ContactSenderType.ADMIN,
            sender_user=admin_user,
            message=message,
        )

        # Update contact status
        if contact.status == ContactStatus.PENDING:
            contact.status = ContactStatus.IN_PROGRESS
        if not contact.replied_at:
            contact.replied_at = datetime.now(timezone.utc)
        contact.last_message_at = reply.created_at
        contact.save(update_fields=["status", "replied_at", "last_message_at", "updated_at"])

        # Dispatch email asynchronously AFTER the transaction commits.
        # Using on_commit() guarantees:
        #   1. The Celery task only fires once the DB row is durably written.
        #   2. The SMTP network call never holds the DB row lock.
        #   3. Celery can retry on transient SMTP failures without data loss.
        if contact.source not in {"mobile_help", "mobile_app"}:
            from core.admin_dashboard.tasks import send_contact_reply_email

            transaction.on_commit(
                lambda: send_contact_reply_email.apply_async(
                    args=[str(contact.id), message],
                    queue=settings.CELERY_QUEUE_EMAIL,
                    priority=settings.CELERY_EMAIL_TASK_PRIORITY,
                )
            )
        elif contact.requester_user_id:
            transaction.on_commit(
                lambda: _notify_support_reply(
                    user_id=contact.requester_user_id,
                    contact_id=contact.id,
                    message=message,
                )
            )

        log_admin_action(
            admin_user=admin_user,
            action="CONTACT_REPLIED",
            target_type="ContactMessage",
            target_id=str(contact.id),
            details={"reply_id": str(reply.id)},
            ip_address=ip_address,
        )

        logger.info(
            "contact_replied",
            extra={
                "contact_id": contact_id,
                "admin_id": str(admin_user.id),
            },
        )

        return {"success": True, "contact": _contact_to_dict(contact)}

    @staticmethod
    @transaction.atomic
    def update_contact_status(
        contact_id: str,
        status: str,
        admin_user=None,
        ip_address: str = "",
    ) -> dict:
        """Update the status of a contact message."""
        from core.admin_dashboard.models import ContactMessage, ContactStatus

        valid_statuses = [s.value for s in ContactStatus]
        if status not in valid_statuses:
            raise AdminError(
                message=f"Invalid status. Must be one of: {', '.join(valid_statuses)}.",
                code=ErrorCode.VALIDATION_ERROR,
            )

        contact = ContactMessage.objects.select_for_update().filter(id=contact_id).first()

        if not contact:
            raise AdminError(
                message="Contact message not found.",
                code=ErrorCode.CONTACT_NOT_FOUND,
            )

        contact.status = status
        contact.save(update_fields=["status"])

        log_admin_action(
            admin_user=admin_user,
            action="CONTACT_STATUS_UPDATED",
            target_type="ContactMessage",
            target_id=str(contact.id),
            details={"new_status": status},
            ip_address=ip_address,
        )

        return {"success": True, "contact": _contact_to_dict(contact)}

    @staticmethod
    def submit_message(
        name: str,
        email: str,
        message: str,
        ip_address: str = "",
        source: str = "mobile_app",
        brand: str = "",
        topic: str = "",
        requester_user=None,
    ) -> dict:
        """Public endpoint: submit a contact/support message.

        Rate-limited to 5 submissions per IP per 10-minute rolling window to
        prevent spam abuse and protect SMTP send budgets.

        Called from the mobile app — no admin auth required.
        """
        from django.core.cache import cache

        from core.admin_dashboard.models import ContactMessage

        # ── Rate limit guard ──────────────────────────────────────────────
        # Uses the existing Redis cache — no new infrastructure needed.
        # The rolling window is implemented with cache.set on the first hit
        # and cache.incr on each subsequent hit within the TTL window.
        rate_limit = 5  # max submissions per window
        window_seconds = 600  # 10-minute rolling window

        if ip_address:
            cache_key = f"contact_submit_rate:{ip_address}"
            submission_count = cache.get(cache_key, 0)

            if submission_count >= rate_limit:
                logger.warning(
                    "contact_submit_rate_limited",
                    extra={"ip_address": ip_address, "count": submission_count},
                )
                raise AdminError(
                    message="Too many submissions. Please wait before trying again.",
                    code=ErrorCode.RATE_LIMIT_EXCEEDED,
                )

            if submission_count == 0:
                # First request in the window — set with full TTL
                cache.set(cache_key, 1, window_seconds)
            else:
                # Subsequent requests — increment without resetting the TTL
                cache.incr(cache_key)
        # ─────────────────────────────────────────────────────────────────

        if not name or not email or not message:
            raise AdminError(
                message="Name, email, and message are required.",
                code=ErrorCode.VALIDATION_ERROR,
            )

        from core.admin_dashboard.models import (
            ContactConversationMessage,
            ContactSenderType,
        )

        with transaction.atomic():
            contact = ContactMessage.objects.create(
                name=name.strip(),
                email=email.strip().lower(),
                message=message.strip(),
                requester_user=requester_user,
                topic=(topic or "").strip()[:100],
                source=(source or "mobile_app").strip()[:50],
                brand=(brand or "").strip()[:50],
                ip_address=ip_address or None,
            )
            initial_message = ContactConversationMessage.objects.create(
                contact=contact,
                sender_type=ContactSenderType.USER,
                sender_user=requester_user,
                message=message.strip(),
            )
            contact.last_message_at = initial_message.created_at
            contact.save(update_fields=["last_message_at", "updated_at"])

        logger.info(
            "contact_submitted",
            extra={"contact_id": str(contact.id), "email": email},
        )

        return {
            "success": True,
            "contact_id": str(contact.id),
            "message": "Your message has been received. We'll get back to you soon.",
            "contact": _contact_to_dict(contact),
        }

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


# ─────────────────────────────────────────
# Private helpers
# ─────────────────────────────────────────


def _contact_to_dict(contact) -> dict:
    """Convert ContactMessage to admin-facing dict."""
    replies = []
    if hasattr(contact, "conversation_messages"):
        messages = list(contact.conversation_messages.all())
        for r in messages[1:]:
            sent_by_name = ""
            if r.sender_user:
                sent_by_name = r.sender_user.full_name or r.sender_user.username
            elif r.sender_type == "ADMIN":
                sent_by_name = "Ziona Support"
            else:
                sent_by_name = contact.name
            replies.append(
                {
                    "id": str(r.id),
                    "message": r.message,
                    "sent_by_name": sent_by_name,
                    "sender_type": r.sender_type,
                    "sent_at": r.created_at.isoformat(),
                }
            )

    return {
        "id": str(contact.id),
        "name": contact.name,
        "email": contact.email,
        "message": contact.message,
        "topic": contact.topic,
        "requester_username": (
            contact.requester_user.username if getattr(contact, "requester_user", None) else ""
        ),
        "source": contact.source,
        "brand": contact.brand,
        "status": contact.status,
        "replies": replies,
        "replied_at": contact.replied_at.isoformat() if contact.replied_at else None,
        "created_at": contact.created_at.isoformat(),
        "updated_at": contact.updated_at.isoformat(),
        "last_message_at": (
            contact.last_message_at.isoformat() if contact.last_message_at else None
        ),
    }


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


def _notify_support_reply(*, user_id, contact_id, message: str) -> None:
    from core.notifications.models import NotificationType
    from core.notifications.services import create_notification

    create_notification(
        user_id=user_id,
        type_str=NotificationType.SUPPORT_REPLY,
        reference_id=contact_id,
        reference_type="ContactMessage",
        title="Ziona Support replied",
        message=message[:240],
        respect_preferences=False,
        bypass_duplicate_check=True,
        push_data={
            "screen": "SupportConversation",
            "contactId": str(contact_id),
            "deepLink": f"ziona://support/{contact_id}",
        },
    )

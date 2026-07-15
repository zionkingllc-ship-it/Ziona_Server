"""
Contact/Support service — ticket management and admin replies.

Replies send email via Django's send_mail (Ensend backend).
"""

import logging
from datetime import datetime, timezone

from django.conf import settings
from django.db import transaction

from core.admin_dashboard.help_conversations import HelpConversationOps
from core.admin_dashboard.permissions import log_admin_action
from core.shared.exceptions import AdminError, ErrorCode

logger = logging.getLogger("core.admin_dashboard")


class ContactService(HelpConversationOps):
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

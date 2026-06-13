"""
Contact/Support service — ticket management and admin replies.

Replies send email via Django's send_mail (Ensend backend).
"""

import logging
from datetime import datetime, timezone

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
            .prefetch_related("replies__sent_by")
            .order_by("-created_at")
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

        Creates a ContactReply record, sends the reply via Django send_mail,
        and updates the contact status to IN_PROGRESS.
        """
        from core.admin_dashboard.models import ContactMessage, ContactReply, ContactStatus

        contact = ContactMessage.objects.select_for_update().filter(id=contact_id).first()

        if not contact:
            raise AdminError(
                message="Contact message not found.",
                code=ErrorCode.CONTACT_NOT_FOUND,
            )

        # Create reply record
        reply = ContactReply.objects.create(
            contact=contact,
            message=message,
            sent_by=admin_user,
        )

        # Update contact status
        if contact.status == ContactStatus.PENDING:
            contact.status = ContactStatus.IN_PROGRESS
        if not contact.replied_at:
            contact.replied_at = datetime.now(timezone.utc)
        contact.save(update_fields=["status", "replied_at"])

        # Dispatch email asynchronously AFTER the transaction commits.
        # Using on_commit() guarantees:
        #   1. The Celery task only fires once the DB row is durably written.
        #   2. The SMTP network call never holds the DB row lock.
        #   3. Celery can retry on transient SMTP failures without data loss.
        from core.admin_dashboard.tasks import send_contact_reply_email

        transaction.on_commit(lambda: send_contact_reply_email.delay(str(contact.id), message))

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
        """Submit an in-app help/support message and mirror it into the admin queue."""
        from core.admin_dashboard.models import ContactMessage

        category = None
        if category_slug:
            category = get_help_category(category_slug)
            if not category:
                raise AdminError(
                    message="Help category not found.",
                    code=ErrorCode.CATEGORY_NOT_FOUND,
                )

        resolved_name = (name or "").strip()
        resolved_email = (email or "").strip().lower()

        if user:
            resolved_name = user.full_name or user.username or user.email.split("@")[0]
            resolved_email = user.email

        if not resolved_name or not resolved_email:
            raise AdminError(
                message="Name and email are required when no authenticated user is available.",
                code=ErrorCode.VALIDATION_ERROR,
            )

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
            .prefetch_related("replies__sent_by")
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

        normalized_email = (email or "").strip().lower()
        if user:
            normalized_email = user.email.strip().lower()

        if not user and not normalized_email:
            return []

        filters = Q(source__in=["mobile_help", "mobile_app"])
        if user:
            filters &= Q(requester_user=user) | Q(email=normalized_email)
        else:
            filters &= Q(email=normalized_email)

        qs = (
            ContactMessage.objects.select_related("requester_user")
            .filter(filters)
            .prefetch_related("replies__sent_by")
        )
        if status_filter:
            qs = qs.filter(status=status_filter)

        return [_contact_to_help_thread(contact) for contact in qs.order_by("-created_at")]

    @staticmethod
    @transaction.atomic
    def resolve_help_conversation(contact_id: str, *, user=None, email: str = "") -> dict:
        """Mark a support conversation as resolved for the requesting viewer."""
        from core.admin_dashboard.models import ContactMessage, ContactStatus

        normalized_email = (email or "").strip().lower()
        if user:
            normalized_email = user.email.strip().lower()

        contact = ContactMessage.objects.select_for_update().filter(id=contact_id).first()
        if not contact:
            raise AdminError(
                message="Contact message not found.",
                code=ErrorCode.CONTACT_NOT_FOUND,
            )

        belongs_to_viewer = bool(
            (user and contact.requester_user_id == user.id)
            or (normalized_email and contact.email.strip().lower() == normalized_email)
        )

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
    if hasattr(contact, "replies"):
        for r in contact.replies.all():
            sent_by_name = ""
            if r.sent_by:
                sent_by_name = r.sent_by.full_name or r.sent_by.username
            replies.append(
                {
                    "id": str(r.id),
                    "message": r.message,
                    "sent_by_name": sent_by_name,
                    "sent_at": r.sent_at.isoformat(),
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
    }


def _contact_to_help_thread(contact) -> dict:
    """Convert a contact message and replies into a mobile-friendly conversation."""
    messages = [
        {
            "id": str(contact.id),
            "message": contact.message,
            "sent_at": contact.created_at.isoformat(),
            "sender_type": "USER",
            "sender_name": contact.name,
        }
    ]

    for reply in contact.replies.all():
        messages.append(
            {
                "id": str(reply.id),
                "message": reply.message,
                "sent_at": reply.sent_at.isoformat(),
                "sender_type": "ADMIN",
                "sender_name": (
                    reply.sent_by.full_name or reply.sent_by.username
                    if reply.sent_by
                    else "Ziona Support"
                ),
            }
        )

    return {
        "id": str(contact.id),
        "topic": contact.topic,
        "status": contact.status,
        "created_at": contact.created_at.isoformat(),
        "replied_at": contact.replied_at.isoformat() if contact.replied_at else None,
        "messages": messages,
    }

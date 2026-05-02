"""
Contact/Support service — ticket management and admin replies.

Replies send email via Django's send_mail (Ensend backend).
"""

import logging
from datetime import datetime, timezone

from django.db import transaction

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

        qs = ContactMessage.objects.prefetch_related("replies__sent_by").order_by("-created_at")

        if status_filter:
            qs = qs.filter(status=status_filter)

        if search:
            from django.db.models import Q

            qs = qs.filter(
                Q(name__icontains=search)
                | Q(email__icontains=search)
                | Q(message__icontains=search)
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
    def submit_message(name: str, email: str, message: str) -> dict:
        """Public endpoint: submit a contact/support message.

        Called from the mobile app — no admin auth required.
        """
        from core.admin_dashboard.models import ContactMessage

        if not name or not email or not message:
            raise AdminError(
                message="Name, email, and message are required.",
                code=ErrorCode.VALIDATION_ERROR,
            )

        contact = ContactMessage.objects.create(
            name=name,
            email=email,
            message=message,
        )

        logger.info(
            "contact_submitted",
            extra={"contact_id": str(contact.id), "email": email},
        )

        return {
            "success": True,
            "contact_id": str(contact.id),
            "message": "Your message has been received. We'll get back to you soon.",
        }


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
        "status": contact.status,
        "replies": replies,
        "replied_at": contact.replied_at.isoformat() if contact.replied_at else None,
        "created_at": contact.created_at.isoformat(),
    }

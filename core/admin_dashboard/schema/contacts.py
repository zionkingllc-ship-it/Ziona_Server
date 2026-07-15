"""Admin contact/support tickets + public contact submission.

Split from the former core/admin_dashboard/schema.py (no contract change).
"""

from __future__ import annotations

import strawberry
from strawberry.types import Info

from core.admin_dashboard.permissions import admin_required
from core.admin_dashboard.schema.help_center import (
    HelpConversationType,
    _map_help_conversation,
)
from core.shared.request_utils import get_client_ip
from core.shared.types import ErrorType


@strawberry.type
class ContactReplyType:
    """A reply to a contact message."""

    id: str
    message: str
    sent_by_name: str = strawberry.field(name="sentByName")
    sender_type: str = strawberry.field(name="senderType")
    sent_at: str = strawberry.field(name="sentAt")


@strawberry.type
class AdminContactType:
    """Admin-facing contact message representation."""

    id: str
    name: str
    email: str
    message: str
    topic: str = ""
    requester_username: str = strawberry.field(name="requesterUsername", default="")
    source: str
    brand: str
    status: str
    replies: list[ContactReplyType]
    replied_at: str | None = strawberry.field(name="repliedAt", default=None)
    created_at: str = strawberry.field(name="createdAt")
    updated_at: str = strawberry.field(name="updatedAt")
    last_message_at: str | None = strawberry.field(name="lastMessageAt", default=None)


@strawberry.type
class ContactSummaryType:
    """Summary counts for contacts."""

    total: int
    pending: int
    in_progress: int = strawberry.field(name="inProgress")
    resolved: int


@strawberry.type
class AdminContactsPaginatedType:
    """Paginated contacts response."""

    contacts: list[AdminContactType]
    total_count: int = strawberry.field(name="totalCount")
    page: int
    page_size: int = strawberry.field(name="pageSize")
    total_pages: int = strawberry.field(name="totalPages")
    summary: ContactSummaryType


@strawberry.type
class AdminContactReplyPayload:
    """Response for contact reply mutation."""

    success: bool
    contact: AdminContactType | None = None
    error: ErrorType | None = None


@strawberry.type
class AdminContactPayload:
    """Response for contact status mutation."""

    success: bool
    contact: AdminContactType | None = None
    error: ErrorType | None = None


@strawberry.type
class SubmitContactPayload:
    """Response for public contact submission."""

    success: bool
    contact_id: str | None = strawberry.field(name="contactId", default=None)
    message: str | None = None
    contact: HelpConversationType | None = None
    error: ErrorType | None = None


def _map_contact(data: dict) -> AdminContactType:
    replies = [
        ContactReplyType(
            id=r["id"],
            message=r["message"],
            sent_by_name=r.get("sent_by_name", ""),
            sender_type=r.get("sender_type", "ADMIN"),
            sent_at=r["sent_at"],
        )
        for r in data.get("replies", [])
    ]

    return AdminContactType(
        id=data["id"],
        name=data["name"],
        email=data["email"],
        message=data["message"],
        topic=data.get("topic", ""),
        requester_username=data.get("requester_username", ""),
        source=data.get("source", ""),
        brand=data.get("brand", ""),
        status=data["status"],
        replies=replies,
        replied_at=data.get("replied_at"),
        created_at=data["created_at"],
        updated_at=data.get("updated_at", data["created_at"]),
        last_message_at=data.get("last_message_at"),
    )


@strawberry.type
class ContactsAdminQueries:
    @strawberry.field(name="adminContacts", description="List contact messages.")
    @admin_required
    def admin_contacts(
        self,
        info: Info,
        status: str = "",
        search: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> AdminContactsPaginatedType:
        from core.admin_dashboard.contact_services import ContactService

        result = ContactService.list_contacts(
            status_filter=status,
            search=search,
            page=page,
            page_size=page_size,
        )

        return AdminContactsPaginatedType(
            contacts=[_map_contact(c) for c in result["contacts"]],
            total_count=result["total_count"],
            page=result["page"],
            page_size=result["page_size"],
            total_pages=result["total_pages"],
            summary=ContactSummaryType(**result["summary"]),
        )


@strawberry.type
class ContactsAdminMutations:
    @strawberry.mutation(
        name="adminReplyToContact",
        description="Reply to a contact message.",
    )
    @admin_required
    def admin_reply_to_contact(
        self,
        info: Info,
        contact_id: str,
        message: str,
    ) -> AdminContactReplyPayload:
        from core.admin_dashboard.contact_services import ContactService
        from core.shared.exceptions import AdminError

        admin_user = info.context.admin_user
        ip = getattr(info.context, "admin_ip", "")

        try:
            result = ContactService.reply_to_contact(
                contact_id=contact_id,
                message=message,
                admin_user=admin_user,
                ip_address=ip,
            )
            return AdminContactReplyPayload(
                success=True,
                contact=_map_contact(result["contact"]),
            )
        except AdminError as e:
            return AdminContactReplyPayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )

    @strawberry.mutation(
        name="adminUpdateContactStatus",
        description="Update contact message status.",
    )
    @admin_required
    def admin_update_contact_status(
        self,
        info: Info,
        contact_id: str,
        status: str,
    ) -> AdminContactPayload:
        from core.admin_dashboard.contact_services import ContactService
        from core.shared.exceptions import AdminError

        admin_user = info.context.admin_user
        ip = getattr(info.context, "admin_ip", "")

        try:
            result = ContactService.update_contact_status(
                contact_id=contact_id,
                status=status,
                admin_user=admin_user,
                ip_address=ip,
            )
            return AdminContactPayload(
                success=True,
                contact=_map_contact(result["contact"]),
            )
        except AdminError as e:
            return AdminContactPayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )

    @strawberry.mutation(
        name="submitContactMessage",
        description="Public: submit a contact/support message (no auth required).",
    )
    def submit_contact_message(
        self,
        info: Info,
        message: str,
        name: str | None = None,
        email: str | None = None,
        category_slug: str | None = None,
    ) -> SubmitContactPayload:
        from core.admin_dashboard.contact_services import ContactService
        from core.shared.exceptions import AdminError
        from core.users.models import User
        from core.users.schema import _get_authenticated_user_id

        try:
            ip_address = get_client_ip(info.context.request, default="")
            user_id = _get_authenticated_user_id(info)
            user = None
            if user_id:
                user = User.objects.filter(id=user_id, deleted_at__isnull=True).first()

            if user or category_slug:
                result = ContactService.submit_help_message(
                    message=message,
                    category_slug=category_slug or "",
                    ip_address=ip_address,
                    user=user,
                    name=name or "",
                    email=email or "",
                )
            else:
                result = ContactService.submit_message(
                    name=name or "",
                    email=email or "",
                    message=message,
                    ip_address=ip_address,
                )
            return SubmitContactPayload(
                success=True,
                contact_id=result["contact_id"],
                message=result["message"],
                contact=_map_help_conversation(result["contact"])
                if result.get("contact")
                else None,
            )
        except AdminError as e:
            return SubmitContactPayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )

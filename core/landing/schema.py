"""
Landing page GraphQL schema.

Public (no auth):
  Queries:  privacyPolicy, termsOfService, communityGuidelines,
            appDownloadLinks, companyStats
  Mutations: submitContact, joinWaitlist

Admin-protected:
  Mutations: updateLegalDocument
"""

from enum import Enum

import strawberry
from strawberry.types import Info

from core.shared.exceptions import AdminError, ErrorCode
from core.shared.request_utils import get_client_ip
from core.shared.types import ErrorType

# ──────────────────────────────────────────────────────────────
# Strawberry Enums
# ──────────────────────────────────────────────────────────────


@strawberry.enum
class ContactBrand(Enum):
    ZIONA = "ZIONA"
    ZIONKING = "ZIONKING"


@strawberry.enum
class WaitlistBrand(Enum):
    ZIONA = "ZIONA"
    ZIONKING = "ZIONKING"


@strawberry.enum
class LegalDocumentTypeEnum(Enum):
    PRIVACY_POLICY = "privacy_policy"
    TERMS_OF_SERVICE = "terms_of_service"
    COMMUNITY_GUIDELINES = "community_guidelines"


# ──────────────────────────────────────────────────────────────
# Output types
# ──────────────────────────────────────────────────────────────


@strawberry.type
class ContactPayload:
    """Response for submitContact mutation."""

    success: bool
    ticket_id: str | None = strawberry.field(name="ticketId", default=None)
    error: ErrorType | None = None


@strawberry.type
class WaitlistPayload:
    """Response for joinWaitlist mutation."""

    success: bool
    already_registered: bool = strawberry.field(name="alreadyRegistered", default=False)
    error: ErrorType | None = None


@strawberry.type
class LegalDocumentType:
    """A versioned legal document."""

    content: str
    document_url: str = strawberry.field(name="documentUrl")
    document_type: str = strawberry.field(name="documentType")
    version: str
    last_updated: str = strawberry.field(name="lastUpdated")


@strawberry.type
class AppLinksType:
    """Active app store download links."""

    ios_url: str | None = strawberry.field(name="iosUrl", default=None)
    android_url: str | None = strawberry.field(name="androidUrl", default=None)


@strawberry.type
class CompanyStatsType:
    """Public platform statistics."""

    active_users: str = strawberry.field(name="activeUsers", default="")
    downloads: str = strawberry.field(name="downloads", default="")
    last_updated: str = strawberry.field(name="lastUpdated", default="")


@strawberry.type
class LegalDocumentPayload:
    """Response for updateLegalDocument mutation."""

    success: bool
    document: LegalDocumentType | None = None
    error: ErrorType | None = None


# ──────────────────────────────────────────────────────────────
# Helper: extract client IP from Strawberry Info
# ──────────────────────────────────────────────────────────────


def _get_ip(info: Info) -> str:
    return get_client_ip(info.context.request)


# ──────────────────────────────────────────────────────────────
# Helper: map LegalDocument model → LegalDocumentType
# ──────────────────────────────────────────────────────────────


def _map_legal_doc(doc) -> LegalDocumentType:
    return LegalDocumentType(
        content=doc.content or "",
        document_url=doc.document_url or "",
        document_type=doc.document_type or "application/pdf",
        version=doc.version,
        last_updated=doc.published_at.isoformat()
        if doc.published_at
        else doc.created_at.isoformat(),
    )


# ──────────────────────────────────────────────────────────────
# Queries
# ──────────────────────────────────────────────────────────────


@strawberry.type
class LandingQueries:
    """Public landing page queries — no authentication required."""

    @strawberry.field(
        name="privacyPolicy",
        description="Returns the currently active Privacy Policy.",
    )
    def privacy_policy(self) -> LegalDocumentType:
        from core.landing.models import LegalDocumentType as ModelLegalDocumentType
        from core.landing.services import LegalDocumentService

        doc = LegalDocumentService.get_active(ModelLegalDocumentType.PRIVACY_POLICY)
        return _map_legal_doc(doc)

    @strawberry.field(
        name="termsOfService",
        description="Returns the currently active Terms of Service.",
    )
    def terms_of_service(self) -> LegalDocumentType:
        from core.landing.models import LegalDocumentType as ModelLegalDocumentType
        from core.landing.services import LegalDocumentService

        doc = LegalDocumentService.get_active(ModelLegalDocumentType.TERMS_OF_SERVICE)
        return _map_legal_doc(doc)

    @strawberry.field(
        name="communityGuidelines",
        description="Returns the currently active Community Guidelines.",
    )
    def community_guidelines(self) -> LegalDocumentType:
        from core.landing.models import LegalDocumentType as ModelLegalDocumentType
        from core.landing.services import LegalDocumentService

        doc = LegalDocumentService.get_active(ModelLegalDocumentType.COMMUNITY_GUIDELINES)
        return _map_legal_doc(doc)

    @strawberry.field(
        name="appDownloadLinks",
        description="Returns active iOS and Android app store URLs.",
    )
    def app_download_links(self) -> AppLinksType:
        from core.landing.services import AppLinkService

        links = AppLinkService.get_links()
        return AppLinksType(
            ios_url=links.get("ios_url"),
            android_url=links.get("android_url"),
        )

    @strawberry.field(
        name="companyStats",
        description="Returns public platform statistics (updated hourly).",
    )
    def company_stats(self) -> CompanyStatsType:
        from core.landing.services import CompanyStatService

        stats = CompanyStatService.get_stats()
        return CompanyStatsType(
            active_users=stats.get("active_users", ""),
            downloads=stats.get("downloads", ""),
            last_updated=stats.get("last_updated", ""),
        )


# ──────────────────────────────────────────────────────────────
# Mutations
# ──────────────────────────────────────────────────────────────


@strawberry.type
class LandingMutations:
    """Landing page mutations."""

    @strawberry.mutation(
        name="submitContact",
        description="Submit a contact form message for ZIONA or ZIONKING.",
    )
    def submit_contact(
        self,
        info: Info,
        brand: ContactBrand,
        name: str,
        email: str,
        message: str,
        honeypot: str = "",
    ) -> ContactPayload:
        from core.landing.services import ContactService
        from core.users.schema import _get_authenticated_user_id

        try:
            if _get_authenticated_user_id(info):
                raise AdminError(
                    "Use the authenticated in-app help flow for support conversations.",
                    ErrorCode.USE_HELP_SUPPORT_FLOW,
                )
            result = ContactService.submit(
                brand=brand.value,
                name=name,
                email=email,
                message=message,
                ip_address=_get_ip(info),
                honeypot=honeypot,
            )
            return ContactPayload(success=True, ticket_id=result["ticket_id"])
        except AdminError as e:
            return ContactPayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )

    @strawberry.mutation(
        name="joinWaitlist",
        description="Add an email to the ZIONA or ZIONKING waitlist.",
    )
    def join_waitlist(
        self,
        info: Info,
        brand: WaitlistBrand,
        email: str,
    ) -> WaitlistPayload:
        from core.landing.services import WaitlistService

        try:
            result = WaitlistService.join(
                brand=brand.value,
                email=email,
                ip_address=_get_ip(info),
            )
            return WaitlistPayload(
                success=True,
                already_registered=result["already_registered"],
            )
        except AdminError as e:
            return WaitlistPayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )

    @strawberry.mutation(
        name="updateLegalDocument",
        description="Publish a new version of a legal document. Admin only.",
    )
    def update_legal_document(
        self,
        info: Info,
        type: LegalDocumentTypeEnum,
        version: str,
        content: str = "",
        document_url: str = "",
        document_type: str = "application/pdf",
    ) -> LegalDocumentPayload:
        # Inline admin guard — @admin_required is designed as a decorator;
        # for non-dashboard mutations we validate manually via get_admin_user.
        from core.admin_dashboard.permissions import get_admin_user
        from core.landing.services import LegalDocumentService

        admin_user = get_admin_user(info)
        if admin_user is None:
            return LegalDocumentPayload(
                success=False,
                error=ErrorType(code="UNAUTHORIZED", message="Admin access required."),
            )

        try:
            doc = LegalDocumentService.update(
                doc_type=type.value,
                content=content,
                version=version,
                document_url=document_url,
                document_type=document_type,
            )
            return LegalDocumentPayload(success=True, document=_map_legal_doc(doc))
        except AdminError as e:
            return LegalDocumentPayload(
                success=False,
                error=ErrorType(code=e.code, message=e.message),
            )

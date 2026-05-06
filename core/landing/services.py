"""
Landing page service layer.

All business logic lives here. Schema resolvers call services; services call
the ORM and EmailService. Rate limiting uses LuaLimiter.check_rate_limit()
with the same key namespace as the existing RateLimitMiddleware.
"""

import logging

from django.db import transaction
from django.utils import timezone

from core.shared.exceptions import AdminError, ErrorCode
from core.shared.redis_lua import LuaLimiter

logger = logging.getLogger("core.landing")


# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────


def _check_rate_limit(action: str, ip: str, max_requests: int, window_seconds: int) -> None:
    """Raise AdminError(RATE_LIMIT_EXCEEDED) if the IP has exceeded the limit.

    Uses LuaLimiter.check_rate_limit() with key pattern:
      ratelimit:{action}:{ip}
    which matches the existing middleware namespace.
    """
    key = f"ratelimit:{action}:{ip}"
    is_limited, retry_after = LuaLimiter.check_rate_limit(key, max_requests, window_seconds)
    if is_limited:
        raise AdminError(
            f"Too many requests. Try again in {retry_after} seconds.",
            ErrorCode.RATE_LIMIT_EXCEEDED,
        )


def _format_count(value: int) -> str:
    """Format an integer into a human-readable display string.

    Examples: 999 → '999', 1000 → '1k+', 1234 → '1.2k+', 1000000 → '1M+'
    """
    if value >= 1_000_000:
        m = value / 1_000_000
        return f"{m:.1f}M+".replace(".0M+", "M+")
    if value >= 1_000:
        k = value / 1_000
        return f"{k:.1f}k+".replace(".0k+", "k+")
    return str(value)


# ──────────────────────────────────────────────────────────────
# ContactService
# ──────────────────────────────────────────────────────────────


class ContactService:
    """Handles dual-brand contact form submissions."""

    # 3 submissions per IP per 15 minutes
    _RATE_LIMIT = (3, 900)

    @staticmethod
    def submit(
        brand: str,
        name: str,
        email: str,
        message: str,
        ip_address: str,
        honeypot: str = "",
    ) -> dict:
        """Validate, persist and route a contact form submission.

        Steps:
          1. Rate limit (3 / 15 min per IP via LuaLimiter)
          2. Honeypot check (save as spam=True, return silently)
          3. Server-side field validation
          4. Persist ContactSubmission
          5. on_commit: send auto-reply + internal notification

        Returns:
            {"ticket_id": str}
        """
        from core.landing.models import ContactSubmission

        _check_rate_limit("contact", ip_address, *ContactService._RATE_LIMIT)

        # Honeypot: if populated, save as spam and return fake success
        is_spam = bool(honeypot and honeypot.strip())

        name = name.strip()
        email = email.strip().lower()
        message = message.strip()

        if not name:
            raise AdminError("Name is required.", ErrorCode.VALIDATION_ERROR)
        if not email:
            raise AdminError("Email is required.", ErrorCode.VALIDATION_ERROR)
        if len(message) < 10:
            raise AdminError("Message must be at least 10 characters.", ErrorCode.VALIDATION_ERROR)
        if len(message) > 2000:
            raise AdminError("Message must not exceed 2000 characters.", ErrorCode.VALIDATION_ERROR)

        with transaction.atomic():
            submission = ContactSubmission.objects.create(
                brand=brand.upper(),
                name=name,
                email=email,
                message=message,
                ip_address=ip_address or None,
                honeypot=honeypot,
                is_spam=is_spam,
            )

            if not is_spam:
                submission_brand = submission.brand

                def _send_emails() -> None:
                    from core.emails.services import EmailService

                    EmailService.send_contact_auto_reply(name, email, submission_brand)
                    EmailService.send_internal_contact_notification(
                        name, email, message, submission_brand
                    )

                transaction.on_commit(_send_emails)

        logger.info(
            "contact_submitted",
            extra={
                "submission_id": str(submission.id),
                "brand": brand,
                "is_spam": is_spam,
            },
        )
        return {"ticket_id": str(submission.id)}


# ──────────────────────────────────────────────────────────────
# WaitlistService
# ──────────────────────────────────────────────────────────────


class WaitlistService:
    """Handles dual-brand waitlist signups."""

    # 3 signups per IP per hour
    _RATE_LIMIT = (3, 3600)

    @staticmethod
    def join(brand: str, email: str, ip_address: str) -> dict:
        """Add an email to the waitlist (idempotent via get_or_create).

        Returns:
            {"already_registered": bool}
        """
        import re

        from core.landing.models import WaitlistEntry

        _check_rate_limit("waitlist", ip_address, *WaitlistService._RATE_LIMIT)

        email = email.strip().lower()
        if not re.match(r"^[^@]+@[^@]+\.[^@]+$", email):
            raise AdminError("Invalid email address.", ErrorCode.VALIDATION_ERROR)

        brand_upper = brand.upper()

        entry, created = WaitlistEntry.objects.get_or_create(
            brand=brand_upper,
            email=email,
            defaults={"ip_address": ip_address or None},
        )

        if created:

            def _send_confirmation() -> None:
                from core.emails.services import EmailService

                EmailService.send_waitlist_confirmation(email, brand_upper)

            transaction.on_commit(_send_confirmation)
            logger.info(
                "waitlist_joined",
                extra={"brand": brand_upper, "email": email},
            )

        return {"already_registered": not created}


# ──────────────────────────────────────────────────────────────
# LegalDocumentService
# ──────────────────────────────────────────────────────────────


class LegalDocumentService:
    """Manages versioned legal documents."""

    @staticmethod
    def get_active(doc_type: str) -> "LegalDocument":  # noqa: F821
        """Return the currently active document for a given type.

        Raises AdminError(NOT_FOUND) if no active document exists.
        """
        from core.landing.models import LegalDocument

        try:
            return LegalDocument.objects.get(type=doc_type, is_active=True)
        except LegalDocument.DoesNotExist:
            raise AdminError(f"No active {doc_type} document found.", ErrorCode.NOT_FOUND) from None

    @staticmethod
    def update(doc_type: str, content: str, version: str) -> "LegalDocument":  # noqa: F821
        """Publish a new version of a legal document.

        Deactivates the current active document (if any) and creates a new
        active one. Wrapped in a transaction so both steps are atomic.
        """
        from core.landing.models import LegalDocument

        with transaction.atomic():
            # Deactivate current active version (if any)
            LegalDocument.objects.filter(type=doc_type, is_active=True).update(is_active=False)

            doc = LegalDocument.objects.create(
                type=doc_type,
                content=content,
                version=version,
                published_at=timezone.now(),
                is_active=True,
            )

        logger.info(
            "legal_document_updated",
            extra={"type": doc_type, "version": version, "id": str(doc.id)},
        )
        return doc


# ──────────────────────────────────────────────────────────────
# AppLinkService
# ──────────────────────────────────────────────────────────────


class AppLinkService:
    """Returns active app store URLs."""

    @staticmethod
    def get_links() -> dict:
        """Return active iOS and Android store URLs."""
        from core.landing.models import AppStoreLink

        links = AppStoreLink.objects.filter(is_active=True).values("platform", "url")
        result = {"ios_url": None, "android_url": None}
        for link in links:
            if link["platform"] == "ios":
                result["ios_url"] = link["url"]
            elif link["platform"] == "android":
                result["android_url"] = link["url"]
        return result


# ──────────────────────────────────────────────────────────────
# CompanyStatService
# ──────────────────────────────────────────────────────────────


class CompanyStatService:
    """Returns company statistics for the public landing page."""

    @staticmethod
    def get_stats() -> dict:
        """Return all active company stats as a dict keyed by stat key."""
        from core.landing.models import CompanyStat

        stats = CompanyStat.objects.values("key", "display_value", "updated_at")
        result: dict = {}
        last_updated = None
        for s in stats:
            result[s["key"]] = s["display_value"]
            if last_updated is None or s["updated_at"] > last_updated:
                last_updated = s["updated_at"]

        result["last_updated"] = last_updated.isoformat() if last_updated else ""
        return result

    @staticmethod
    def refresh() -> None:
        """Recompute and persist all company stats. Called by Celery task."""
        from django.contrib.auth import get_user_model

        from core.landing.models import CompanyStat

        user_model = get_user_model()

        active_users = user_model.objects.filter(is_active=True).count()
        CompanyStat.objects.update_or_create(
            key="active_users",
            defaults={
                "value": active_users,
                "display_value": _format_count(active_users),
            },
        )

        # 'downloads' is a manually managed stat — seed only, not computed
        CompanyStat.objects.get_or_create(
            key="downloads",
            defaults={"value": 0, "display_value": "Coming soon"},
        )

        logger.info("company_stats_refreshed", extra={"active_users": active_users})

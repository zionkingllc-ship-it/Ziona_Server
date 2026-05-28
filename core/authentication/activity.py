"""Authentication activity helpers.

Centralizes updates to fields used by admin analytics so every token-issuing
auth path records activity consistently.
"""

from __future__ import annotations

from django.utils import timezone


def record_successful_auth(user, ip_address: str | None = None) -> None:
    """Record a successful authentication or authenticated session refresh."""
    user.last_login = timezone.now()
    update_fields = ["last_login", "updated_at"]

    if ip_address is not None:
        user.last_login_ip = ip_address
        update_fields.append("last_login_ip")

    user.save(update_fields=update_fields)

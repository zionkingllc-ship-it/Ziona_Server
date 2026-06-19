"""Shared helpers for trusted request metadata extraction."""

from __future__ import annotations

import ipaddress

from django.conf import settings
from django.http import HttpRequest


def get_client_ip(request: HttpRequest, *, default: str = "unknown") -> str:
    """Return the best client IP while only trusting known proxy hops.

    We only honor `CF-Connecting-IP` / `X-Forwarded-For` when the immediate
    peer (`REMOTE_ADDR`) is within `TRUSTED_PROXY_CIDRS`. Otherwise we fall
    back to the direct remote address and ignore any user-controlled headers.
    """

    remote_ip = _parse_ip(request.META.get("REMOTE_ADDR", ""))
    if remote_ip is None:
        return default

    if not _is_trusted_proxy(remote_ip):
        return str(remote_ip)

    cloudflare_ip = _parse_ip(request.META.get("HTTP_CF_CONNECTING_IP", ""))
    if cloudflare_ip is not None:
        return str(cloudflare_ip)

    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    for candidate in forwarded_for.split(","):
        forwarded_ip = _parse_ip(candidate.strip())
        if forwarded_ip is not None:
            return str(forwarded_ip)

    return str(remote_ip)


def _parse_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Parse a literal IP string into an address object."""
    if not value:
        return None
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def _is_trusted_proxy(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True when the immediate peer is one of our trusted proxies."""
    for cidr in getattr(settings, "TRUSTED_PROXY_CIDRS", []):
        try:
            if address in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False

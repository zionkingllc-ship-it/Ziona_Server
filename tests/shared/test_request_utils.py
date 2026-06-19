from django.test import RequestFactory, override_settings

from core.shared.request_utils import get_client_ip


@override_settings(TRUSTED_PROXY_CIDRS=["10.0.0.0/8"])
def test_untrusted_remote_addr_ignores_forwarded_headers():
    request = RequestFactory().get(
        "/",
        REMOTE_ADDR="203.0.113.10",
        HTTP_X_FORWARDED_FOR="198.51.100.22, 10.0.0.20",
        HTTP_CF_CONNECTING_IP="198.51.100.21",
    )

    assert get_client_ip(request) == "203.0.113.10"


@override_settings(TRUSTED_PROXY_CIDRS=["10.0.0.0/8"])
def test_trusted_proxy_prefers_cf_connecting_ip():
    request = RequestFactory().get(
        "/",
        REMOTE_ADDR="10.0.0.20",
        HTTP_X_FORWARDED_FOR="198.51.100.22, 10.0.0.20",
        HTTP_CF_CONNECTING_IP="198.51.100.21",
    )

    assert get_client_ip(request) == "198.51.100.21"


@override_settings(TRUSTED_PROXY_CIDRS=["10.0.0.0/8"])
def test_trusted_proxy_falls_back_to_forwarded_for():
    request = RequestFactory().get(
        "/",
        REMOTE_ADDR="10.0.0.20",
        HTTP_X_FORWARDED_FOR="198.51.100.22, 10.0.0.20",
    )

    assert get_client_ip(request) == "198.51.100.22"

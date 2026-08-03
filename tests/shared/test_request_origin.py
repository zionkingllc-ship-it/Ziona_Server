"""get_request_origin: normalize the requesting web origin to scheme://host."""

from django.test import RequestFactory

from core.shared.request_utils import get_request_origin


def test_reads_origin_header():
    req = RequestFactory().post("/graphql/", HTTP_ORIGIN="https://ziona.app")
    assert get_request_origin(req) == "https://ziona.app"


def test_falls_back_to_referer_and_strips_path_and_query():
    req = RequestFactory().post("/graphql/", HTTP_REFERER="https://zionking.org/contact?ref=x")
    assert get_request_origin(req) == "https://zionking.org"


def test_prefers_origin_over_referer():
    req = RequestFactory().post(
        "/graphql/",
        HTTP_ORIGIN="https://admin.ziona.app",
        HTTP_REFERER="https://zionking.org/x",
    )
    assert get_request_origin(req) == "https://admin.ziona.app"


def test_empty_when_no_headers():
    req = RequestFactory().post("/graphql/")
    assert get_request_origin(req) == ""

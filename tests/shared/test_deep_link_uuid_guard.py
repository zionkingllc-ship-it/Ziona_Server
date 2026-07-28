"""Deep-link hardening (#19): single-item fetches must reject a malformed id
with their normal not-found path instead of an unhandled ValidationError."""

import uuid

import pytest

from core.shared.utils import parse_uuid


def test_parse_uuid_accepts_valid_and_rejects_garbage():
    valid = uuid.uuid4()
    assert parse_uuid(str(valid)) == valid
    assert parse_uuid(valid) == valid
    assert parse_uuid("not-a-uuid") is None
    assert parse_uuid("12345") is None
    assert parse_uuid("") is None
    assert parse_uuid(None) is None


@pytest.mark.django_db
def test_single_item_services_reject_malformed_ids_gracefully():
    from core.circles.anchor_services import get_anchor_by_id
    from core.circles.services import get_circle_by_id, get_circle_post
    from core.posts.services import PostService
    from core.profiles.services import ProfileService
    from core.shared.exceptions import PostError, ProfileError, ZionaError

    with pytest.raises(PostError):
        PostService.get_post("not-a-uuid")
    with pytest.raises(ZionaError):
        get_circle_post("not-a-uuid")
    with pytest.raises(ZionaError):
        get_anchor_by_id("not-a-uuid")
    with pytest.raises(ProfileError):
        ProfileService.get_user_profile("not-a-uuid")
    # get_circle_by_id returns None (non-raising contract) rather than crashing.
    assert get_circle_by_id("not-a-uuid") is None


@pytest.mark.django_db
def test_share_preview_returns_404_for_malformed_id():
    from django.test import RequestFactory

    from core.posts.views import share_preview

    request = RequestFactory().get("/post/not-a-uuid/")
    response = share_preview(request, "not-a-uuid")

    assert response.status_code == 404

"""Multi-image circle posts must preserve the creator's selected order (Bug 2)."""

import pytest
from django.contrib.auth import get_user_model

from core.circles.models import Circle, CircleMembership
from core.circles.services.circle_posts import create_circle_post, get_circle_post
from core.media.models import MediaFile

User = get_user_model()


def _ready_image(user, name):
    return MediaFile.objects.create(
        user=user,
        file_name=name,
        storage_path=name,
        media_type="image",
        file_size=1024,
        status="ready",
    )


@pytest.fixture
def circle_member(db):
    user = User.objects.create_user(email="cmember@example.com", password="password123")
    circle = Circle.objects.create(
        name="Order Circle",
        description="x",
        cover_image="https://example.com/cover.jpg",
        created_by=user,
    )
    CircleMembership.objects.create(circle=circle, user=user, role="admin")
    return user, circle


@pytest.mark.django_db
def test_create_circle_post_preserves_selected_media_order(circle_member):
    user, circle = circle_member
    a = _ready_image(user, "a.jpg")
    b = _ready_image(user, "b.jpg")
    c = _ready_image(user, "c.jpg")
    selected = [str(c.id), str(a.id), str(b.id)]

    post = create_circle_post(user_id=str(user.id), circle_id=str(circle.id), media_ids=selected)

    # Create read-back reflects the selected order…
    assert [str(m.id) for m in post.media_files.all()] == selected

    # …and a fresh read does too.
    fetched = get_circle_post(str(post.id), viewer_id=str(user.id))
    assert [str(m.id) for m in fetched.media_files.all()] == selected

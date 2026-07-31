"""Multi-image posts must preserve the creator's selected order (Bug 2).

The client sends `media_ids` in the chosen display order. That order must
survive creation (persisted as `position`) and every read path, rather than
collapsing to media upload time.
"""

import pytest

from core.media.models import MediaFile
from core.posts.services import PostService


def _ready_image(user, name):
    return MediaFile.objects.create(
        user=user,
        file_name=name,
        storage_path=name,
        media_type="image",
        file_size=1024,
        status="ready",
    )


@pytest.mark.django_db
def test_create_post_preserves_selected_media_order(create_user):
    user = create_user()
    # Uploaded a, b, c (created_at ascending). Selected order is deliberately
    # different so upload-time ordering would produce the wrong result.
    a = _ready_image(user, "a.jpg")
    b = _ready_image(user, "b.jpg")
    c = _ready_image(user, "c.jpg")
    selected = [str(c.id), str(a.id), str(b.id)]

    result = PostService.create_post(
        user_id=str(user.id),
        post_type="image",
        caption="ordered",
        media_ids=selected,
    )

    # Create response reflects the selected order (not upload order)…
    assert [item.id for item in result.media.items] == selected
    assert [item.order for item in result.media.items] == [0, 1, 2]

    # …and a fresh read returns the same order.
    fetched = PostService.get_post(str(result.id))
    assert [item.id for item in fetched.media.items] == selected


@pytest.mark.django_db
def test_feed_queryset_prefetches_media_in_selected_order(create_user):
    """The feed builds DTOs from `_base_post_queryset().prefetch(...).all()`; that
    prefetch cache must already be in selected order (and carry `order`)."""
    from core.feed.services.ranking import _base_post_queryset

    author = create_user(email="author@example.com", username="author")
    a = _ready_image(author, "a.jpg")
    b = _ready_image(author, "b.jpg")
    c = _ready_image(author, "c.jpg")
    selected = [str(b.id), str(c.id), str(a.id)]

    created = PostService.create_post(
        user_id=str(author.id),
        post_type="image",
        caption="feed ordered",
        media_ids=selected,
    )

    post = _base_post_queryset().get(id=created.id)
    cached = list(post.media_files.all())  # prefetch cache — no extra query
    assert [str(m.id) for m in cached] == selected
    assert [m.order for m in cached] == [0, 1, 2]

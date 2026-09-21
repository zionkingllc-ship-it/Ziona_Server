"""The anchor snapshot a circle post carries.

An anchor is live for 24 hours and hard-deleted 5 days after it expires
(purge_expired_anchors). A post written against it has to keep rendering the
reference card long after both, so the anchor is copied onto the post at
creation and never read again.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from core.circles.models import Anchor, Circle, CircleMembership, CirclePost
from core.circles.schema.posts import CirclePostAnchorReferenceType, CirclePostType
from core.circles.services.circle_posts import create_circle_post
from core.shared.exceptions import ZionaError
from core.users.models import User


@pytest.fixture
def circle_and_anchor(db):
    author = User.objects.create_user(email="snap@example.com", password="password123")
    circle = Circle.objects.create(name="Snapshot Circle", description="x")
    CircleMembership.objects.create(circle=circle, user=author, role="member")
    anchor = Anchor.objects.create(
        circle=circle,
        created_by=author,
        anchor_type="bible_verse",
        title="Be still",
        content="A word for today",
        scripture_book="Psalms",
        scripture_chapter=46,
        scripture_verse_start=10,
        scripture_verse_end=11,
        scripture_text="Be still, and know that I am God.",
        background_colors=["#A8D5A2", "#EDEDED"],
        background_image="https://cdn.example.com/bg.jpg",
        anchor_image="https://cdn.example.com/img.jpg",
        published_at=timezone.now(),
        expires_at=timezone.now() + timedelta(days=1),
    )
    return circle, anchor, author


def _reference(post) -> CirclePostAnchorReferenceType | None:
    return CirclePostType.from_db_model(post).anchor_reference


def test_snapshot_copies_every_presentation_field(circle_and_anchor):
    circle, anchor, author = circle_and_anchor

    post = create_circle_post(
        user_id=str(author.id),
        circle_id=str(circle.id),
        text="This spoke to me",
        anchor_id=str(anchor.id),
    )

    assert post.anchor_id == anchor.id
    assert post.anchor_type == "bible_verse"
    assert post.anchor_title == "Be still"
    assert post.anchor_content == "A word for today"
    assert post.anchor_background_colors == ["#A8D5A2", "#EDEDED"]
    assert post.anchor_background_image == "https://cdn.example.com/bg.jpg"
    # bible_reference is composed, not copied — the Anchor model has no such field.
    assert post.anchor_bible_reference == "Psalms 46:10-11"
    assert post.anchor_bible_text == "Be still, and know that I am God."
    assert post.anchor_expires_at == anchor.expires_at


def test_media_precedence_prefers_video_then_image(circle_and_anchor):
    circle, anchor, author = circle_and_anchor
    anchor.anchor_video = "https://cdn.example.com/clip.mp4"
    anchor.media_url = "https://cdn.example.com/legacy.jpg"
    anchor.save(update_fields=["anchor_video", "media_url"])

    post = create_circle_post(
        user_id=str(author.id),
        circle_id=str(circle.id),
        text="x",
        anchor_id=str(anchor.id),
    )

    assert post.anchor_media_url == "https://cdn.example.com/clip.mp4"


def test_post_without_an_anchor_has_no_reference(circle_and_anchor):
    circle, _anchor, author = circle_and_anchor

    post = create_circle_post(user_id=str(author.id), circle_id=str(circle.id), text="plain")

    assert post.anchor_id is None
    assert _reference(post) is None
    # JSONField default, not null — the resolver must not trip over it.
    assert post.anchor_background_colors == []


def test_unknown_anchor_id_is_rejected_and_creates_no_post(circle_and_anchor):
    circle, _anchor, author = circle_and_anchor
    before = CirclePost.objects.count()

    with pytest.raises(ZionaError) as exc:
        create_circle_post(
            user_id=str(author.id),
            circle_id=str(circle.id),
            text="x",
            anchor_id="0f14d0ab-9605-4a62-a9e4-5ed26688389b",
        )

    assert exc.value.code == "ANCHOR_NOT_FOUND"
    assert CirclePost.objects.count() == before


def test_malformed_anchor_id_is_rejected(circle_and_anchor):
    circle, _anchor, author = circle_and_anchor

    with pytest.raises(ZionaError) as exc:
        create_circle_post(
            user_id=str(author.id), circle_id=str(circle.id), text="x", anchor_id="not-a-uuid"
        )

    assert exc.value.code == "ANCHOR_NOT_FOUND"


def test_anchor_from_another_circle_is_rejected(circle_and_anchor):
    """Without the circle_id filter a member could republish another circle's anchor."""
    circle, _anchor, author = circle_and_anchor
    other_circle = Circle.objects.create(name="Other", description="x")
    foreign_anchor = Anchor.objects.create(
        circle=other_circle,
        created_by=author,
        anchor_type="devotional",
        title="Private",
        content="Members only",
        published_at=timezone.now(),
        expires_at=timezone.now() + timedelta(days=1),
    )

    with pytest.raises(ZionaError) as exc:
        create_circle_post(
            user_id=str(author.id),
            circle_id=str(circle.id),
            text="x",
            anchor_id=str(foreign_anchor.id),
        )

    assert exc.value.code == "ANCHOR_NOT_FOUND"


def test_snapshot_survives_the_anchor_being_deleted(circle_and_anchor):
    circle, anchor, author = circle_and_anchor
    post = create_circle_post(
        user_id=str(author.id), circle_id=str(circle.id), text="x", anchor_id=str(anchor.id)
    )

    Anchor.objects.filter(id=anchor.id).delete()

    reference = _reference(post)
    assert reference is not None
    assert reference.title == "Be still"
    assert reference.bible_reference == "Psalms 46:10-11"


def test_soft_deleted_anchor_can_still_be_referenced(circle_and_anchor):
    circle, anchor, author = circle_and_anchor
    anchor.deleted_at = timezone.now()
    anchor.save(update_fields=["deleted_at"])

    post = create_circle_post(
        user_id=str(author.id), circle_id=str(circle.id), text="x", anchor_id=str(anchor.id)
    )

    assert post.anchor_title == "Be still"


def test_navigation_is_offered_while_the_anchor_still_exists(circle_and_anchor):
    circle, anchor, author = circle_and_anchor
    post = create_circle_post(
        user_id=str(author.id), circle_id=str(circle.id), text="x", anchor_id=str(anchor.id)
    )

    reference = _reference(post)
    assert reference.anchor_id == str(anchor.id)


def test_navigation_stops_once_the_anchor_has_been_purged(circle_and_anchor):
    """purge_expired_anchors deletes at expires_at + 5 days. The card stays."""
    circle, anchor, author = circle_and_anchor
    post = create_circle_post(
        user_id=str(author.id), circle_id=str(circle.id), text="x", anchor_id=str(anchor.id)
    )
    CirclePost.objects.filter(id=post.id).update(
        anchor_expires_at=timezone.now() - timedelta(days=6)
    )
    post.refresh_from_db()

    reference = _reference(post)
    assert reference is not None
    assert reference.anchor_id is None  # navigation blocked
    assert reference.title == "Be still"  # card intact
    assert reference.background_colors == ["#A8D5A2", "#EDEDED"]


def test_reference_never_queries_the_anchor_table(circle_and_anchor):
    """Resolving a feed page must not cost one anchor lookup per post."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    circle, anchor, author = circle_and_anchor
    posts = [
        create_circle_post(
            user_id=str(author.id), circle_id=str(circle.id), text=f"p{i}", anchor_id=str(anchor.id)
        )
        for i in range(5)
    ]

    with CaptureQueriesContext(connection) as captured:
        references = [CirclePostType.from_db_model(p).anchor_reference for p in posts]

    assert all(r is not None for r in references)
    anchor_queries = [q for q in captured.captured_queries if "anchors" in q["sql"]]
    assert anchor_queries == []

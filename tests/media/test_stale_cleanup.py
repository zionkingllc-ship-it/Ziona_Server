"""Stale-media cleanup must never delete a GCS object still referenced by content.

Regression for the disappearing-images bug: circle covers (and other bare-URL
media) were stored as URL strings while their MediaFile row stayed PENDING, so
the status-only cleanup deleted the live blob. The cleanup is now reference-aware.
"""

import pytest
from django.utils import timezone

from core.circles.models import Circle, CirclePost
from core.media.models import MediaFile
from core.media.tasks import _public_url, cleanup_stale_media_uploads


@pytest.fixture
def fake_bucket(monkeypatch):
    """Mock the GCS bucket; record every blob path that gets deleted."""
    deleted_paths: list[str] = []

    class FakeBlob:
        def __init__(self, path):
            self.path = path

        def delete(self):
            deleted_paths.append(self.path)

    class FakeBucket:
        def blob(self, path):
            return FakeBlob(path)

    monkeypatch.setattr("core.media.tasks._get_gcs_bucket", lambda: FakeBucket())
    return deleted_paths


def _stale_media(user, storage_path, *, status="pending", minutes=16):
    media = MediaFile.objects.create(
        user=user,
        file_name="img.jpg",
        file_type="image/jpeg",
        file_size=2048,
        media_type="image",
        storage_path=storage_path,
        status=status,
    )
    # updated_at is auto_now; backdate it past the 15-minute staleness cutoff.
    MediaFile.objects.filter(id=media.id).update(
        updated_at=timezone.now() - timezone.timedelta(minutes=minutes)
    )
    media.refresh_from_db()
    return media


@pytest.mark.django_db
def test_referenced_circle_cover_is_protected_and_healed(create_user, fake_bucket):
    user = create_user()
    media = _stale_media(user, "uploads/u/images/cover.jpg")
    Circle.objects.create(
        name="Faith Circle",
        description="A circle",
        cover_image=_public_url(media.storage_path),  # bare-URL reference
        created_by=user,
    )

    cleanup_stale_media_uploads()

    media.refresh_from_db()
    assert media.storage_path not in fake_bucket  # blob NOT deleted
    assert media.status == "ready"  # self-healed


@pytest.mark.django_db
def test_twin_blob_is_protected(create_user, fake_bucket):
    """A stale relative-key row must not be deleted while a READY row (media_urls
    path) owns the same object via a full-URL storage_path."""
    user = create_user()
    stale = _stale_media(user, "uploads/u/images/twin.jpg")
    MediaFile.objects.create(
        user=user,
        file_name="twin.jpg",
        file_type="image/jpeg",
        file_size=2048,
        media_type="image",
        storage_path=_public_url(stale.storage_path),  # same object, full URL
        status="ready",
    )

    cleanup_stale_media_uploads()

    stale.refresh_from_db()
    assert stale.storage_path not in fake_bucket
    assert stale.status == "ready"


@pytest.mark.django_db
def test_circlepost_media_url_reference_protects(create_user, fake_bucket):
    user = create_user()
    media = _stale_media(user, "uploads/u/images/post.jpg")
    circle = Circle.objects.create(
        name="Circle", description="d", cover_image="https://x/y.jpg", created_by=user
    )
    CirclePost.objects.create(
        circle=circle, user=user, text="hi", media_url=_public_url(media.storage_path)
    )

    cleanup_stale_media_uploads()

    media.refresh_from_db()
    assert media.storage_path not in fake_bucket
    assert media.status == "ready"


@pytest.mark.django_db
def test_unreferenced_stale_is_deleted_and_failed(create_user, fake_bucket):
    user = create_user()
    media = _stale_media(user, "uploads/u/images/orphan.jpg")

    cleanup_stale_media_uploads()

    media.refresh_from_db()
    assert media.storage_path in fake_bucket  # blob deleted
    assert media.status == "failed"
    assert media.processing_failed_stage == "stale_cleanup"


@pytest.mark.django_db
def test_recent_pending_is_untouched(create_user, fake_bucket):
    user = create_user()
    media = _stale_media(user, "uploads/u/images/recent.jpg", minutes=5)  # inside window

    assert cleanup_stale_media_uploads() == 0
    media.refresh_from_db()
    assert media.storage_path not in fake_bucket
    assert media.status == "pending"

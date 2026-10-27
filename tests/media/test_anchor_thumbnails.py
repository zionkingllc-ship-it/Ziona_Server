from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from core.admin_dashboard.anchor_services import AnchorManagementService
from core.circles.anchor_services import create_anchor as create_public_anchor
from core.circles.models import Anchor, Circle, CircleMembership
from core.media.models import MediaFile
from core.media.tasks import _generate_image_thumbnail, generate_anchor_thumbnail
from core.media.thumbnails import resolve_media_thumbnail_url


def _media_file(
    user,
    *,
    storage_path="uploads/test/images/source.jpg",
    thumbnail_path="thumbnails/test/source.jpg",
):
    return MediaFile.objects.create(
        user=user,
        file_name="source.jpg",
        file_type="image/jpeg",
        file_size=1024,
        media_type="image",
        storage_path=storage_path,
        thumbnail_path=thumbnail_path,
        status="ready",
    )


def _anchor(circle, user, *, media_url, anchor_type="image", anchor_thumbnail=""):
    now = timezone.now()
    return Anchor.objects.create(
        circle=circle,
        created_by=user,
        anchor_type=anchor_type,
        title="Thumbnail anchor",
        media_url=media_url,
        anchor_image=media_url,
        anchor_thumbnail=anchor_thumbnail,
        anchor_status="draft",
        published_at=now,
        expires_at=now + timedelta(days=1),
    )


@pytest.mark.django_db
def test_resolve_media_thumbnail_url_uses_generated_media_thumbnail(create_user):
    user = create_user()
    media = _media_file(user)

    assert resolve_media_thumbnail_url("", media.url) == media.thumbnail_url


@pytest.mark.django_db
def test_admin_create_anchor_populates_thumbnail_from_media(create_user):
    user = create_user()
    circle = Circle.objects.create(name="Admin Thumb Circle", created_by=user)
    media = _media_file(user)

    anchor = AnchorManagementService.create_anchor(
        circle_id=str(circle.id),
        anchor_type="image",
        title="Admin image",
        media_url=media.url,
        anchor_thumbnail=media.url,
        admin_user=user,
    )

    assert anchor["anchor_thumbnail"] == media.thumbnail_url


@pytest.mark.django_db
def test_public_create_anchor_populates_thumbnail_from_media(create_user):
    user = create_user()
    circle = Circle.objects.create(name="Public Thumb Circle", created_by=user)
    CircleMembership.objects.create(circle=circle, user=user, role="admin")
    media = _media_file(user)

    anchor = create_public_anchor(
        creator_id=str(user.id),
        circle_id=str(circle.id),
        anchor_type="image",
        title="Public image",
        media_url=media.url,
        anchor_thumbnail=media.url,
    )

    assert anchor.anchor_thumbnail == media.thumbnail_url


@pytest.mark.django_db
def test_generate_anchor_thumbnail_from_owned_image(create_user, monkeypatch, settings):
    user = create_user()
    circle = Circle.objects.create(name="Generation Circle", created_by=user)
    media_path = "uploads/test/images/full.jpg"
    media_url = f"https://storage.googleapis.com/{settings.GCP_STORAGE_BUCKET}/{media_path}"
    anchor = _anchor(circle, user, media_url=media_url, anchor_thumbnail=media_url)

    downloaded = []
    uploaded = []

    def fake_download(storage_path, destination):
        downloaded.append(storage_path)
        destination.write_bytes(b"source")

    def fake_generate(source_path, thumbnail_path, max_dim):
        thumbnail_path.write_bytes(b"thumb")

    def fake_upload(storage_path, source_path, content_type):
        uploaded.append((storage_path, content_type))

    monkeypatch.setattr("core.media.tasks._download_blob", fake_download)
    monkeypatch.setattr("core.media.tasks._generate_image_thumbnail_file", fake_generate)
    monkeypatch.setattr("core.media.tasks._upload_blob", fake_upload)

    result = generate_anchor_thumbnail(str(anchor.id))

    anchor.refresh_from_db()
    expected_path = f"thumbnails/anchors/{anchor.id}.jpg"
    assert result == str(anchor.id)
    assert downloaded == [media_path]
    assert uploaded == [(expected_path, "image/jpeg")]
    assert anchor.anchor_thumbnail == (
        f"https://storage.googleapis.com/{settings.GCP_STORAGE_BUCKET}/{expected_path}"
    )


@pytest.mark.django_db
def test_generate_anchor_thumbnail_skips_external_url(create_user, monkeypatch):
    user = create_user()
    circle = Circle.objects.create(name="External Circle", created_by=user)
    anchor = _anchor(circle, user, media_url="https://example.com/image.jpg")
    downloaded = []
    monkeypatch.setattr(
        "core.media.tasks._download_blob",
        lambda *args: downloaded.append(args),
    )

    result = generate_anchor_thumbnail(str(anchor.id))

    anchor.refresh_from_db()
    assert result == str(anchor.id)
    assert downloaded == []
    assert anchor.anchor_thumbnail == ""


@pytest.mark.django_db
def test_generated_media_thumbnail_updates_referencing_anchor(create_user, monkeypatch):
    user = create_user()
    media = _media_file(user, thumbnail_path="")
    circle = Circle.objects.create(name="Sync Circle", created_by=user)
    anchor = _anchor(circle, user, media_url=media.url, anchor_thumbnail=media.url)

    monkeypatch.setattr(
        "core.media.tasks._download_blob",
        lambda storage_path, destination: destination.write_bytes(b"source"),
    )
    monkeypatch.setattr(
        "core.media.tasks._generate_image_thumbnail_file",
        lambda source, thumbnail, max_dim: thumbnail.write_bytes(b"thumb"),
    )
    monkeypatch.setattr("core.media.tasks._upload_blob", lambda *args: None)

    _generate_image_thumbnail(media)

    media.refresh_from_db()
    anchor.refresh_from_db()
    assert media.thumbnail_path == f"thumbnails/{user.id}/{media.id}.jpg"
    assert anchor.anchor_thumbnail == media.thumbnail_url


@pytest.mark.django_db
def test_backfill_command_resolves_existing_media(create_user):
    user = create_user()
    media = _media_file(user)
    circle = Circle.objects.create(name="Backfill Circle", created_by=user)
    anchor = _anchor(circle, user, media_url=media.url, anchor_thumbnail=media.url)

    call_command("backfill_anchor_thumbnails", "--limit", "10")

    anchor.refresh_from_db()
    assert anchor.anchor_thumbnail == media.thumbnail_url

import hashlib
from datetime import timedelta

import pytest
from django.core.cache import cache
from django.core.management import call_command
from django.utils import timezone

from core.admin_dashboard.anchor_services import AnchorManagementService
from core.circles.anchor_services import create_anchor as create_public_anchor
from core.circles.models import Anchor, Circle, CircleMembership
from core.media.models import MediaFile
from core.media.tasks import (
    _generate_image_thumbnail,
    generate_anchor_thumbnail,
    sync_media_anchor_thumbnails,
)
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
    expected_path = (
        f"thumbnails/anchors/{anchor.id}/{hashlib.sha256(media_path.encode()).hexdigest()}.jpg"
    )
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
    sync_media_anchor_thumbnails(str(media.id))

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


@pytest.mark.django_db
def test_admin_edit_replaces_old_thumbnail_when_source_changes(create_user):
    user = create_user()
    circle = Circle.objects.create(name="Edited Preview Circle", created_by=user)
    old_media = _media_file(
        user,
        storage_path="uploads/test/images/old.jpg",
        thumbnail_path="thumbnails/test/old.jpg",
    )
    new_media = _media_file(
        user,
        storage_path="uploads/test/images/new.jpg",
        thumbnail_path="thumbnails/test/new.jpg",
    )
    anchor = _anchor(
        circle,
        user,
        media_url=old_media.url,
        anchor_thumbnail=old_media.thumbnail_url,
    )
    anchor.anchor_status = "scheduled"
    anchor.save(update_fields=["anchor_status"])

    AnchorManagementService.edit_scheduled_anchor(
        anchor_id=str(anchor.id),
        admin_user=user,
        media_url=new_media.url,
    )

    anchor.refresh_from_db()
    assert anchor.media_url == new_media.url
    assert anchor.anchor_image == new_media.url
    assert anchor.anchor_thumbnail == new_media.thumbnail_url


@pytest.mark.django_db
def test_admin_edit_preserves_explicit_thumbnail_and_unrelated_edit(create_user):
    user = create_user()
    circle = Circle.objects.create(name="Custom Preview Circle", created_by=user)
    old_media = _media_file(user, storage_path="uploads/test/images/old.jpg")
    new_media = _media_file(user, storage_path="uploads/test/images/new.jpg")
    anchor = _anchor(
        circle,
        user,
        media_url=old_media.url,
        anchor_thumbnail=old_media.thumbnail_url,
    )
    anchor.anchor_status = "scheduled"
    anchor.save(update_fields=["anchor_status"])

    AnchorManagementService.edit_scheduled_anchor(
        anchor_id=str(anchor.id), admin_user=user, title="New title"
    )
    anchor.refresh_from_db()
    assert anchor.anchor_thumbnail == old_media.thumbnail_url

    explicit = "https://example.com/custom-preview.jpg"
    AnchorManagementService.edit_scheduled_anchor(
        anchor_id=str(anchor.id),
        admin_user=user,
        anchor_image=new_media.url,
        anchor_thumbnail=explicit,
    )
    anchor.refresh_from_db()
    assert anchor.media_url == new_media.url
    assert anchor.anchor_thumbnail == explicit


@pytest.mark.django_db
def test_admin_edit_without_new_thumbnail_queues_generation(
    create_user, monkeypatch, django_capture_on_commit_callbacks
):
    user = create_user()
    circle = Circle.objects.create(name="Queued Preview Circle", created_by=user)
    old_media = _media_file(user, storage_path="uploads/test/images/old.jpg")
    new_media = _media_file(user, storage_path="uploads/test/images/new.jpg", thumbnail_path="")
    anchor = _anchor(
        circle,
        user,
        media_url=old_media.url,
        anchor_thumbnail=old_media.thumbnail_url,
    )
    anchor.anchor_status = "scheduled"
    anchor.save(update_fields=["anchor_status"])
    queued = []
    monkeypatch.setattr(
        "core.admin_dashboard.anchor_services.enqueue_anchor_thumbnail", queued.append
    )

    with django_capture_on_commit_callbacks(execute=True):
        AnchorManagementService.edit_scheduled_anchor(
            anchor_id=str(anchor.id), admin_user=user, media_url=new_media.url
        )

    anchor.refresh_from_db()
    assert anchor.anchor_thumbnail == ""
    assert queued == [str(anchor.id)]


@pytest.mark.django_db
def test_old_generation_job_cannot_publish_after_source_edit(create_user, monkeypatch, settings):
    user = create_user()
    circle = Circle.objects.create(name="Race Preview Circle", created_by=user)
    old_path = "uploads/test/images/old.jpg"
    new_path = "uploads/test/images/new.jpg"
    old_url = f"https://storage.googleapis.com/{settings.GCP_STORAGE_BUCKET}/{old_path}"
    new_url = f"https://storage.googleapis.com/{settings.GCP_STORAGE_BUCKET}/{new_path}"
    anchor = _anchor(circle, user, media_url=old_url)
    uploaded = []

    def fake_download(storage_path, destination):
        destination.write_bytes(b"image")
        if storage_path == old_path:
            Anchor.objects.filter(id=anchor.id).update(media_url=new_url, anchor_image=new_url)

    monkeypatch.setattr("core.media.tasks._download_blob", fake_download)
    monkeypatch.setattr(
        "core.media.tasks._generate_image_thumbnail_file",
        lambda source, thumbnail, max_dim: thumbnail.write_bytes(b"thumb"),
    )
    monkeypatch.setattr(
        "core.media.tasks._upload_blob",
        lambda storage_path, source, content_type: uploaded.append(storage_path),
    )

    generate_anchor_thumbnail(str(anchor.id))
    anchor.refresh_from_db()
    assert anchor.anchor_thumbnail == ""

    generate_anchor_thumbnail(str(anchor.id))
    anchor.refresh_from_db()
    assert anchor.anchor_thumbnail.endswith(uploaded[-1])
    assert len(uploaded) == 2
    assert uploaded[0] != uploaded[1]


@pytest.mark.django_db
def test_anchor_sync_enqueue_failure_does_not_fail_media(
    create_user, monkeypatch, django_capture_on_commit_callbacks
):
    user = create_user()
    media = _media_file(user, thumbnail_path="")
    monkeypatch.setattr(
        "core.media.tasks._download_blob",
        lambda storage_path, destination: destination.write_bytes(b"source"),
    )
    monkeypatch.setattr(
        "core.media.tasks._generate_image_thumbnail_file",
        lambda source, thumbnail, max_dim: thumbnail.write_bytes(b"thumb"),
    )
    monkeypatch.setattr("core.media.tasks._upload_blob", lambda *args: None)
    monkeypatch.setattr(
        "core.media.tasks.sync_media_anchor_thumbnails.apply_async",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("broker unavailable")),
    )

    with django_capture_on_commit_callbacks(execute=True):
        _generate_image_thumbnail(media)
    media.refresh_from_db()
    assert media.thumbnail_path
    assert media.status == "ready"


@pytest.mark.django_db
def test_anchor_sync_invalidates_cached_active_preview(create_user):
    user = create_user()
    media = _media_file(user)
    circle = Circle.objects.create(name="Cached Preview Circle", created_by=user)
    anchor = _anchor(circle, user, media_url=media.url, anchor_thumbnail=media.url)
    key = f"active_anchor:{circle.id}"
    cache.set(key, anchor, 300)

    from core.media.thumbnails import sync_anchor_thumbnails

    assert sync_anchor_thumbnails(media) == 1
    assert cache.get(key) is None
    anchor.refresh_from_db()
    assert anchor.anchor_thumbnail == media.thumbnail_url

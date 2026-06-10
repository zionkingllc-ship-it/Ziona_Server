import json
from io import StringIO

import pytest
from django.core.management import call_command
from django.test import Client

from core.media.models import MediaFile
from core.media.services import MediaError, MediaService


@pytest.mark.django_db
def test_generate_upload_url_returns_signed_and_public_urls(
    settings, authenticated_user, monkeypatch
):
    settings.GCP_STORAGE_BUCKET = "ziona-media-test"
    monkeypatch.setattr(
        "core.media.services._generate_gcp_signed_url",
        lambda **kwargs: "https://storage.googleapis.com/signed-upload-url",
    )

    result = MediaService.generate_upload_url(
        user_id=str(authenticated_user["user"].id),
        file_name="circle-cover.jpg",
        file_type="image/jpeg",
        file_size=2048,
    )

    media_file = MediaFile.objects.get(id=result["media_id"])
    assert result["upload_url"] == "https://storage.googleapis.com/signed-upload-url"
    assert result["media_url"] == (
        f"https://storage.googleapis.com/ziona-media-test/{media_file.storage_path}"
    )
    assert result["expires_in"] == settings.GCP_SIGNED_URL_EXPIRY
    assert media_file.status == "pending"


@pytest.mark.django_db
def test_generate_upload_url_signing_failure_does_not_create_orphan_media(
    settings, authenticated_user, monkeypatch
):
    settings.GCP_STORAGE_BUCKET = "ziona-media-test"

    def fail_signing(**kwargs):
        raise RuntimeError("missing credentials")

    monkeypatch.setattr("core.media.services._generate_gcp_signed_url", fail_signing)

    with pytest.raises(MediaError) as exc:
        MediaService.generate_upload_url(
            user_id=str(authenticated_user["user"].id),
            file_name="circle-cover.jpg",
            file_type="image/jpeg",
            file_size=2048,
        )

    assert exc.value.code == "UPLOAD_URL_GENERATION_FAILED"
    assert MediaFile.objects.count() == 0


@pytest.mark.django_db
def test_upload_media_graphql_returns_media_url(settings, authenticated_user, monkeypatch):
    settings.GCP_STORAGE_BUCKET = "ziona-media-test"
    monkeypatch.setattr(
        "core.media.services._generate_gcp_signed_url",
        lambda **kwargs: "https://storage.googleapis.com/signed-upload-url",
    )
    client = Client()
    client.defaults["HTTP_AUTHORIZATION"] = f'Bearer {authenticated_user["access_token"]}'

    response = client.post(
        "/graphql/",
        data=json.dumps(
            {
                "query": """
                mutation UploadMedia($fileName: String!, $fileType: String!, $fileSize: Int!) {
                  uploadMedia(fileName: $fileName, fileType: $fileType, fileSize: $fileSize) {
                    success
                    uploadUrl
                    mediaId
                    mediaUrl
                    status
                    expiresIn
                    error { code message field }
                  }
                }
                """,
                "variables": {
                    "fileName": "circle-cover.jpg",
                    "fileType": "image/jpeg",
                    "fileSize": 2048,
                },
            }
        ),
        content_type="application/json",
    )

    content = json.loads(response.content)
    assert "errors" not in content
    payload = content["data"]["uploadMedia"]
    assert payload["success"] is True
    assert payload["uploadUrl"] == "https://storage.googleapis.com/signed-upload-url"
    assert payload["mediaId"]
    assert payload["mediaUrl"].startswith("https://storage.googleapis.com/ziona-media-test/")
    assert payload["status"] == "pending"
    assert payload["expiresIn"] == settings.GCP_SIGNED_URL_EXPIRY
    assert payload["error"] is None


@pytest.mark.django_db
def test_upload_media_graphql_preserves_mobile_public_url_workaround(
    settings, authenticated_user, monkeypatch
):
    settings.GCP_STORAGE_BUCKET = "ziona-media-test"

    def signed_url(bucket, blob_path, **kwargs):
        return f"https://storage.googleapis.com/{bucket}/{blob_path}?X-Goog-Signature=test"

    monkeypatch.setattr("core.media.services._generate_gcp_signed_url", signed_url)
    client = Client()
    client.defaults["HTTP_AUTHORIZATION"] = f'Bearer {authenticated_user["access_token"]}'

    response = client.post(
        "/graphql/",
        data=json.dumps(
            {
                "query": """
                mutation UploadMedia($fileName: String!, $fileType: String!, $fileSize: Int!) {
                  uploadMedia(fileName: $fileName, fileType: $fileType, fileSize: $fileSize) {
                    success
                    uploadUrl
                    mediaId
                    mediaUrl
                    status
                    error { code message }
                  }
                }
                """,
                "variables": {
                    "fileName": "mobile-feed-image.jpg",
                    "fileType": "image/jpeg",
                    "fileSize": 2048,
                },
            }
        ),
        content_type="application/json",
    )

    content = json.loads(response.content)
    assert "errors" not in content
    payload = content["data"]["uploadMedia"]

    # Mirrors Prime's extractPublicUrl(uploadUrl) workaround.
    signed_path = (
        payload["uploadUrl"].split("?", 1)[0].removeprefix("https://storage.googleapis.com/")
    )
    mobile_derived_public_url = f"https://storage.googleapis.com/{signed_path}"

    assert payload["success"] is True
    assert payload["mediaId"]
    assert payload["status"] == "pending"
    assert payload["mediaUrl"] == mobile_derived_public_url


@pytest.mark.django_db
def test_confirm_upload_graphql_queues_processing(settings, authenticated_user, monkeypatch):
    settings.GCP_STORAGE_BUCKET = "ziona-media-test"
    monkeypatch.setattr(
        "core.media.services._generate_gcp_signed_url",
        lambda **kwargs: "https://storage.googleapis.com/signed-upload-url",
    )
    queued = []

    from core.media.tasks import process_media_upload

    monkeypatch.setattr(process_media_upload, "delay", lambda media_id: queued.append(media_id))

    generated = MediaService.generate_upload_url(
        user_id=str(authenticated_user["user"].id),
        file_name="circle-cover.jpg",
        file_type="image/jpeg",
        file_size=2048,
    )

    client = Client()
    client.defaults["HTTP_AUTHORIZATION"] = f'Bearer {authenticated_user["access_token"]}'
    response = client.post(
        "/graphql/",
        data=json.dumps(
            {
                "query": """
                mutation ConfirmMediaUpload($mediaId: String!) {
                  confirmMediaUpload(mediaId: $mediaId) {
                    success
                    mediaId
                    mediaUrl
                    status
                    error { code message }
                  }
                }
                """,
                "variables": {"mediaId": generated["media_id"]},
            }
        ),
        content_type="application/json",
    )

    content = json.loads(response.content)
    assert "errors" not in content
    payload = content["data"]["confirmMediaUpload"]
    assert payload["success"] is True
    assert payload["mediaId"] == generated["media_id"]
    assert payload["status"] == "processing"
    assert queued == [generated["media_id"]]


@pytest.mark.django_db
def test_media_status_query_returns_processing_state(settings, authenticated_user):
    settings.GCP_STORAGE_BUCKET = "ziona-media-test"
    media = MediaFile.objects.create(
        user=authenticated_user["user"],
        file_name="circle-cover.jpg",
        file_type="image/jpeg",
        file_size=2048,
        media_type="image",
        storage_path="uploads/test/images/circle-cover.jpg",
        status="processing",
    )

    client = Client()
    client.defaults["HTTP_AUTHORIZATION"] = f'Bearer {authenticated_user["access_token"]}'
    response = client.post(
        "/graphql/",
        data=json.dumps(
            {
                "query": """
                query MediaStatus($mediaId: String!) {
                  mediaStatus(mediaId: $mediaId) {
                    success
                    mediaId
                    mediaUrl
                    status
                    error { code message }
                  }
                }
                """,
                "variables": {"mediaId": str(media.id)},
            }
        ),
        content_type="application/json",
    )

    content = json.loads(response.content)
    assert "errors" not in content
    payload = content["data"]["mediaStatus"]
    assert payload["success"] is True
    assert payload["mediaId"] == str(media.id)
    assert payload["status"] == "processing"
    assert payload["mediaUrl"].endswith("/uploads/test/images/circle-cover.jpg")


@pytest.mark.django_db
def test_upload_media_graphql_returns_signing_error(settings, authenticated_user, monkeypatch):
    settings.GCP_STORAGE_BUCKET = "ziona-media-test"

    def fail_signing(**kwargs):
        raise RuntimeError("missing credentials")

    monkeypatch.setattr("core.media.services._generate_gcp_signed_url", fail_signing)
    client = Client()
    client.defaults["HTTP_AUTHORIZATION"] = f'Bearer {authenticated_user["access_token"]}'

    response = client.post(
        "/graphql/",
        data=json.dumps(
            {
                "query": """
                mutation UploadMedia($fileName: String!, $fileType: String!, $fileSize: Int!) {
                  uploadMedia(fileName: $fileName, fileType: $fileType, fileSize: $fileSize) {
                    success
                    mediaId
                    mediaUrl
                    error { code message }
                  }
                }
                """,
                "variables": {
                    "fileName": "circle-cover.jpg",
                    "fileType": "image/jpeg",
                    "fileSize": 2048,
                },
            }
        ),
        content_type="application/json",
    )

    content = json.loads(response.content)
    assert "errors" not in content
    payload = content["data"]["uploadMedia"]
    assert payload["success"] is False
    assert payload["mediaId"] is None
    assert payload["mediaUrl"] is None
    assert payload["error"]["code"] == "UPLOAD_URL_GENERATION_FAILED"
    assert MediaFile.objects.count() == 0


def test_configure_gcs_cors_dry_run_outputs_policy(settings):
    settings.GCP_STORAGE_BUCKET = "ziona-media-test"
    settings.GCS_CORS_ALLOWED_ORIGINS = [
        "http://localhost:3000",
        "https://ziona-app-staging.netlify.app",
    ]
    output = StringIO()

    call_command("configure_gcs_cors", stdout=output)

    text = output.getvalue()
    assert "Dry run only" in text
    assert "ziona-media-test" in text
    assert "http://localhost:3000" in text
    assert '"PUT"' in text

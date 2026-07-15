"""GCS client/bucket access + signed-URL generation.

Split from core/media/services.py (no behavior change). core.media.services
re-exports these so existing import paths and test patch targets keep working.
"""

import logging

from django.conf import settings

logger = logging.getLogger("core.media")


def _generate_gcp_signed_url(
    bucket: str,
    blob_path: str,
    content_type: str,
    expiry_seconds: int,
    method: str = "PUT",
) -> str:
    """Generate a GCP Cloud Storage signed URL.

    Args:
        bucket: GCP bucket name.
        blob_path: Path within the bucket.
        content_type: MIME type for the upload.
        expiry_seconds: URL expiry in seconds.
        method: HTTP method (PUT for upload, GET for download).

    Returns:
        Signed URL string.
    """
    from datetime import timedelta

    client = _get_gcs_client()

    bucket_obj = client.bucket(bucket)
    blob = bucket_obj.blob(blob_path)

    return blob.generate_signed_url(
        version="v4",
        expiration=timedelta(seconds=expiry_seconds),
        method=method,
        content_type=content_type if method == "PUT" else None,
    )


def _get_gcs_client():
    from google.cloud import storage

    credentials_file = settings.GCP_CREDENTIALS_FILE
    if credentials_file:
        return storage.Client.from_service_account_json(credentials_file)
    return storage.Client()


def _get_gcs_bucket():
    return _get_gcs_client().bucket(settings.GCP_STORAGE_BUCKET)

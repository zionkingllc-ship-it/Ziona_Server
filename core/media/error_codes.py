"""Stable media upload error codes shared by upload and processing stages."""

UPLOAD_URL_GENERATION_FAILED = "UPLOAD_URL_GENERATION_FAILED"
UPLOAD_SESSION_CREATION_FAILED = "UPLOAD_SESSION_CREATION_FAILED"
UPLOAD_URL_EXPIRED = "UPLOAD_URL_EXPIRED"
UPLOAD_OBJECT_NOT_FOUND = "UPLOAD_OBJECT_NOT_FOUND"
UPLOAD_GCS_PERMISSION_DENIED = "UPLOAD_GCS_PERMISSION_DENIED"
UPLOAD_GCS_TIMEOUT = "UPLOAD_GCS_TIMEOUT"
UPLOAD_GCS_RATE_LIMITED = "UPLOAD_GCS_RATE_LIMITED"
UPLOAD_GCS_READ_FAILED = "UPLOAD_GCS_READ_FAILED"

# Existing clients may still recognize this pre-contract name. It remains the
# primary code until mobile explicitly adopts the additive canonical metadata.
LEGACY_MEDIA_OBJECT_NOT_FOUND = "MEDIA_OBJECT_NOT_FOUND"


def upload_error_details(code: str, details: dict | None = None) -> dict | None:
    """Attach compatibility metadata without changing the public error shape."""
    result = dict(details or {})
    if code in {LEGACY_MEDIA_OBJECT_NOT_FOUND, UPLOAD_OBJECT_NOT_FOUND}:
        result["canonicalCode"] = UPLOAD_OBJECT_NOT_FOUND
        result["legacyCode"] = LEGACY_MEDIA_OBJECT_NOT_FOUND
    return result or None

"""Configure Google Cloud Storage CORS for browser direct uploads."""

import json
import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

METHODS = ["GET", "HEAD", "PUT", "POST"]
RESPONSE_HEADERS = ["Content-Type", "Content-Length", "Content-Range", "ETag", "x-goog-resumable"]
DEFAULT_MAX_AGE_SECONDS = 3600


class Command(BaseCommand):
    help = "Print or apply the GCS bucket CORS policy required for signed browser uploads."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Apply the CORS policy to the configured GCS bucket. Default is dry-run.",
        )
        parser.add_argument(
            "--check",
            action="store_true",
            help="Compare the live bucket CORS policy with the configured policy and exit on drift.",
        )
        parser.add_argument(
            "--origin",
            action="append",
            dest="origins",
            help="Allowed browser origin. Can be provided multiple times.",
        )
        parser.add_argument(
            "--max-age",
            type=int,
            default=DEFAULT_MAX_AGE_SECONDS,
            help=f"Preflight cache duration in seconds. Default: {DEFAULT_MAX_AGE_SECONDS}.",
        )

    def handle(self, *args, **options):
        if options["apply"] and options["check"]:
            raise CommandError("Use either --apply or --check, not both.")
        bucket_name = settings.GCP_STORAGE_BUCKET
        origins = _normalize_origins(options["origins"] or settings.GCS_CORS_ALLOWED_ORIGINS)
        if not bucket_name:
            raise CommandError("GCP_STORAGE_BUCKET is not configured.")
        if not origins:
            raise CommandError("No GCS CORS origins configured.")

        cors_policy = [
            {
                "origin": origins,
                "method": METHODS,
                "responseHeader": RESPONSE_HEADERS,
                "maxAgeSeconds": options["max_age"],
            }
        ]

        self.stdout.write(f"Bucket: {bucket_name}")
        self.stdout.write("Intended CORS policy:")
        self.stdout.write(json.dumps(cors_policy, indent=2))

        if options["check"]:
            bucket = _build_storage_client().bucket(bucket_name)
            bucket.reload()
            live_policy = _normalize_policy(bucket.cors or [])
            intended_policy = _normalize_policy(cors_policy)
            if live_policy != intended_policy:
                self.stderr.write("Live CORS policy:")
                self.stderr.write(json.dumps(bucket.cors or [], indent=2))
                raise CommandError(f"GCS CORS policy drift detected for gs://{bucket_name}.")
            self.stdout.write(
                self.style.SUCCESS(f"GCS CORS policy matches for gs://{bucket_name}.")
            )
            return

        if not options["apply"]:
            self.stdout.write(
                self.style.WARNING("Dry run only. Re-run with --apply to update the bucket.")
            )
            return

        client = _build_storage_client()
        bucket = client.bucket(bucket_name)
        bucket.reload()
        bucket.cors = cors_policy
        bucket.patch()

        self.stdout.write(self.style.SUCCESS(f"Updated CORS policy for gs://{bucket_name}."))


def _normalize_origins(origins: list[str]) -> list[str]:
    normalized = []
    for origin in origins:
        for item in str(origin).split(","):
            value = item.strip()
            if value and value not in normalized:
                normalized.append(value)
    return normalized


def _normalize_policy(policy: list[dict]) -> list[dict]:
    """Normalize provider/config policy shapes for deterministic drift checks."""
    normalized = []
    for item in policy:
        normalized.append(
            {
                "origin": sorted(_normalize_origins(item.get("origin", []))),
                "method": sorted(item.get("method", [])),
                "responseHeader": sorted(item.get("responseHeader", [])),
                "maxAgeSeconds": int(item.get("maxAgeSeconds", 0)),
            }
        )
    return sorted(normalized, key=lambda item: json.dumps(item, sort_keys=True))


def _build_storage_client():
    from google.cloud import storage

    credentials_file = settings.GCP_CREDENTIALS_FILE
    if credentials_file and os.path.exists(credentials_file):
        return storage.Client.from_service_account_json(credentials_file)
    return storage.Client()

"""Redact legacy user snapshot payloads from admin moderation records."""

from django.core.management.base import BaseCommand

from core.admin_dashboard.user_services import redact_legacy_user_snapshot_payloads


class Command(BaseCommand):
    help = "Redact legacy user_snapshot payloads from audit and moderation JSON metadata."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Persist redactions. Without this flag the command runs in dry-run mode.",
        )

    def handle(self, *args, **options):
        result = redact_legacy_user_snapshot_payloads(dry_run=not options["apply"])
        mode = "apply" if options["apply"] else "dry-run"
        self.stdout.write(
            self.style.SUCCESS(
                f"Legacy snapshot redaction ({mode}) complete: "
                f"{result['redacted_audit_logs']} audit log(s), "
                f"{result['redacted_moderation_actions']} moderation action(s)."
            )
        )

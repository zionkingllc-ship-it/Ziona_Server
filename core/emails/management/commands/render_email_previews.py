from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from core.emails.templates import (
    render_admin_announcement,
    render_moderation_notice,
    render_notification_digest,
    render_reset_password,
    render_support_donation,
    render_verify_email,
    render_welcome_email,
)


class Command(BaseCommand):
    help = "Render branded email templates to local HTML preview files."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output-dir",
            default=".pytest_tmp/email-previews",
            help="Directory where rendered HTML preview files will be written.",
        )

    def handle(self, *args, **options):
        output_dir = Path(options["output_dir"]).resolve()
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise CommandError(f"Could not create preview directory: {output_dir}") from exc

        previews = {
            "verify_email.html": render_verify_email("Brian", "627702")[2],
            "password_reset.html": render_reset_password("Brian", "627702")[2],
            "welcome.html": render_welcome_email("Brian")[2],
            "notification_digest.html": render_notification_digest(
                "Brian",
                [
                    {
                        "actor_name": "Sarah Kim",
                        "content": "followed you",
                        "timestamp": "3 hrs ago",
                    },
                    {
                        "actor_name": "Josh Kay",
                        "content": "mentioned you",
                        "message": "I think this is exactly what the community needs right now.",
                    },
                    {
                        "actor_name": "Mina Lee",
                        "content": "commented on your post",
                        "message": "Thanks for sharing this with everyone.",
                    },
                ],
            )[2],
            "admin_announcement.html": render_admin_announcement(
                user_name="Brian",
                heading="Daily Anchor Update",
                body="A new anchor is available for your circle.",
                circle_name="The Well Circle",
                published_at="Today, 9:30 AM",
                cta_label="Open Dashboard",
            )[2],
            "support_donation.html": render_support_donation(
                "Brian",
                "5.00",
                "May 26, 2026",
            )[2],
            "warning.html": render_moderation_notice(
                "Brian",
                "warned",
                "Violation of community guidelines",
            )[2],
            "suspension.html": render_moderation_notice(
                "Brian",
                "suspended",
                "Repeated violations of community guidelines",
            )[2],
            "reactivation.html": render_moderation_notice("Brian", "reactivated")[2],
        }

        for filename, html in previews.items():
            (output_dir / filename).write_text(html, encoding="utf-8")

        self.stdout.write(
            self.style.SUCCESS(f"Rendered {len(previews)} email previews to {output_dir}")
        )

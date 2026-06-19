"""Validate the media-processing runtime prerequisites."""

from django.core.management.base import BaseCommand, CommandError

from core.media.tasks import get_ffmpeg_runtime_info


class Command(BaseCommand):
    help = "Verify the bundled FFmpeg runtime is resolvable and executable."

    def handle(self, *args, **options):
        info = get_ffmpeg_runtime_info()
        if not info.get("path"):
            raise CommandError("FFmpeg binary could not be resolved.")
        if not info.get("version"):
            raise CommandError("FFmpeg version output could not be read.")

        self.stdout.write(self.style.SUCCESS("FFmpeg runtime is available"))
        self.stdout.write(f"Binary: {info['path']}")
        self.stdout.write(f"Version: {info['version']}")

"""Validate media-processing runtime dependencies."""

import subprocess

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    """Fail fast when FFmpeg cannot be executed by the current runtime."""

    help = "Verify that the bundled FFmpeg binary is available for media processing."

    def handle(self, *args, **options):
        try:
            import imageio_ffmpeg

            ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
            result = subprocess.run(  # noqa: S603
                [ffmpeg_bin, "-version"],  # noqa: S603
                capture_output=True,
                check=False,
                text=True,
                timeout=15,
            )
        except Exception as exc:  # noqa: BLE001
            raise CommandError(f"FFmpeg runtime check failed: {exc}") from exc

        if result.returncode != 0:
            stderr = result.stderr.strip() or result.stdout.strip()
            raise CommandError(f"FFmpeg exited with {result.returncode}: {stderr[:500]}")

        first_line = result.stdout.splitlines()[0] if result.stdout else "ffmpeg available"
        self.stdout.write(self.style.SUCCESS(first_line))

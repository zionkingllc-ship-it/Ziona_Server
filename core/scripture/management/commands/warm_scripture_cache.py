from django.core.management.base import BaseCommand

from core.scripture.services import ScriptureService


class Command(BaseCommand):
    help = "Warm scripture cache with popular verses"

    def handle(self, *args, **options):
        self.stdout.write("Warming scripture cache...")

        try:
            ScriptureService.warm_cache()
            self.stdout.write(self.style.SUCCESS(" Cache warmed successfully"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f" Cache warming failed: {e}"))

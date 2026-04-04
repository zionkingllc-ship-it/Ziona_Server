import logging

from django.core.management.base import BaseCommand

# Import the new decoupled ScriptureImporter service
from core.scripture.importer import ScriptureImporter

logger = logging.getLogger("core.scripture")


class Command(BaseCommand):
    help = "Import Bible translations from JSDelivr CDN into PostgreSQL"

    def add_arguments(self, parser):
        parser.add_argument(
            "translations",
            nargs="+",
            type=str,
            help="Translation codes to import (e.g., kjv asv web rv)",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=1000,
            help="Bulk insert batch size (default: 1000)",
        )
        parser.add_argument(
            "--resume",
            action="store_true",
            help="Resume gracefully from the last imported chapter or skip fully imported translations.",
        )
        parser.add_argument(
            "--async",
            action="store_true",
            dest="run_async",
            help="Dispatch import as a Celery background task",
        )

    def handle(self, *args, **options):
        translations = [t.lower() for t in options["translations"]]
        batch_size = options["batch_size"]
        resume = options["resume"]
        run_async = options.get("run_async", False)

        if run_async:
            from core.scripture.tasks import import_bible_async

            import_bible_async.delay(translations, batch_size=batch_size, resume=resume)
            self.stdout.write(
                self.style.SUCCESS(
                    f"🚀 Dispatched async background import task for: {translations}"
                )
            )
            return

        self.stdout.write("Starting Bible import pipeline...")

        # Instantiate and run the dedicated import pipeline service
        importer = ScriptureImporter(batch_size=batch_size, resume=resume)

        try:
            importer.run_import(translations)
            self.stdout.write(
                self.style.SUCCESS(f" Successfully finished pipeline execution for {translations}.")
            )
        except Exception as e:
            self.stdout.write(self.style.ERROR(f" Fatal Error during pipeline execution: {e}"))

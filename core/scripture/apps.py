import logging

from django.apps import AppConfig

logger = logging.getLogger("core.scripture")


class ScriptureConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core.scripture"

    def ready(self):
        """Only warm CDN manifest, NO database access."""
        import os
        import sys

        # Skip during migrations/tests
        if any(cmd in sys.argv for cmd in ["migrate", "makemigrations", "test"]):
            return

        # Skip during reload
        if os.environ.get("RUN_MAIN") != "true":
            return

        try:
            # Only fetch CDN manifest (no DB)
            from core.scripture.providers.jsdelivr import JSDelivrScriptureService

            JSDelivrScriptureService.get_versions_manifest()
        except Exception as e:
            import logging

            logging.getLogger(__name__).warning(f"CDN warmup failed: {e}")

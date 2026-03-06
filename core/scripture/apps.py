import logging

from django.apps import AppConfig

logger = logging.getLogger("core.scripture")


class ScriptureConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core.scripture"

    def ready(self):
        """Warm up the Bible versions manifest cache on startup."""
        try:
            from core.scripture.providers.jsdelivr import JSDelivrScriptureService

            JSDelivrScriptureService.get_versions_manifest()
        except Exception:
            logger.debug("Failed to warm Bible versions cache", exc_info=True)

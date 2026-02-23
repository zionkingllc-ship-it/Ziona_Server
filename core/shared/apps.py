from django.apps import AppConfig


class SharedConfig(AppConfig):
    """Configuration for the shared utilities app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "core.shared"
    verbose_name = "Shared Utilities"

from django.apps import AppConfig


class ModerationConfig(AppConfig):
    """Configuration for the moderation domain."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "core.moderation"
    verbose_name = "Moderation"

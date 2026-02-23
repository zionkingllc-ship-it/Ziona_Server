"""Media app configuration."""

from django.apps import AppConfig


class MediaConfig(AppConfig):
    """Configuration for the media app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "core.media"
    verbose_name = "Media"

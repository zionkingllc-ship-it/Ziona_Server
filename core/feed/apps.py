from django.apps import AppConfig


class FeedConfig(AppConfig):
    """Configuration for the feed domain."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "core.feed"
    verbose_name = "Feed"

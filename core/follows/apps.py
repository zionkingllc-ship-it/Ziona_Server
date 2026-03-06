from django.apps import AppConfig


class FollowsConfig(AppConfig):
    """Configuration for the follows domain."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "core.follows"
    verbose_name = "Follows"

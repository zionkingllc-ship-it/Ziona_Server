from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    """Configuration for the notifications domain."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "core.notifications"
    verbose_name = "Notifications"

from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    """Configuration for the notifications domain."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "core.notifications"
    verbose_name = "Notifications"

    def ready(self):
        """Import signal handlers when the app is ready."""
        import core.notifications.signals  # noqa

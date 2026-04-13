from django.apps import AppConfig


class AdminDashboardConfig(AppConfig):
    """App configuration for the admin dashboard module."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "core.admin_dashboard"
    verbose_name = "Admin Dashboard"

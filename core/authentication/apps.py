"""Authentication app configuration."""

from django.apps import AppConfig


class AuthenticationConfig(AppConfig):
    """Configuration for the authentication app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "core.authentication"
    verbose_name = "Authentication"

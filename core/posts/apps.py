from django.apps import AppConfig


class PostsConfig(AppConfig):
    """Configuration for the posts domain."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "core.posts"
    verbose_name = "Posts"

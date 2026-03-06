from django.contrib import admin

from core.posts.models import Post, PostMedia


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    """Admin configuration for Posts."""

    list_display = ("id", "user", "post_type", "category", "media_count", "created_at")
    list_filter = ("post_type", "category", "is_mature_content")
    search_fields = ("caption", "user__email", "user__username")
    readonly_fields = ("id", "created_at", "updated_at")
    raw_id_fields = ("user",)


@admin.register(PostMedia)
class PostMediaAdmin(admin.ModelAdmin):
    """Admin configuration for PostMedia."""

    list_display = ("id", "post", "media_type", "order", "created_at")
    list_filter = ("media_type",)
    raw_id_fields = ("post",)

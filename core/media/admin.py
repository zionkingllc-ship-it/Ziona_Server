"""Media admin configuration."""

from django.contrib import admin

from core.media.models import MediaFile


@admin.register(MediaFile)
class MediaFileAdmin(admin.ModelAdmin):
    """Admin configuration for MediaFile model."""

    list_display = ["file_name", "user", "media_type", "status", "file_size", "created_at"]
    list_filter = ["media_type", "status"]
    search_fields = ["file_name", "user__email", "user__username"]
    ordering = ["-created_at"]
    readonly_fields = ["id", "created_at", "updated_at"]

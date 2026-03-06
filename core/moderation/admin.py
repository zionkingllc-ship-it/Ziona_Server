from django.contrib import admin

from core.moderation.models import Report


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    """Admin configuration for Reports."""

    list_display = ("id", "reporter", "reason", "status", "post", "comment", "created_at")
    list_filter = ("status", "reason")
    search_fields = ("description",)
    raw_id_fields = ("reporter", "post", "comment", "reviewed_by")
    readonly_fields = ("id", "created_at")

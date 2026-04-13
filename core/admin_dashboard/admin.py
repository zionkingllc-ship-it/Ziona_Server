from django.contrib import admin

from core.admin_dashboard.models import (
    AdminAuditLog,
    ContactMessage,
    ContactReply,
    DailyAnalytics,
    ModerationAction,
)


@admin.register(AdminAuditLog)
class AdminAuditLogAdmin(admin.ModelAdmin):
    list_display = ("action", "admin_user", "target_type", "target_id", "created_at")
    list_filter = ("action", "target_type")
    search_fields = ("action", "target_id")
    readonly_fields = (
        "id",
        "admin_user",
        "action",
        "target_type",
        "target_id",
        "details",
        "ip_address",
        "created_at",
    )
    ordering = ("-created_at",)


@admin.register(ModerationAction)
class ModerationActionAdmin(admin.ModelAdmin):
    list_display = ("action_type", "user", "admin_user", "created_at")
    list_filter = ("action_type",)
    ordering = ("-created_at",)


@admin.register(DailyAnalytics)
class DailyAnalyticsAdmin(admin.ModelAdmin):
    list_display = ("date", "total_users", "new_users", "dau", "posts_count")
    ordering = ("-date",)


@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "status", "created_at")
    list_filter = ("status",)
    ordering = ("-created_at",)


@admin.register(ContactReply)
class ContactReplyAdmin(admin.ModelAdmin):
    list_display = ("contact", "sent_by", "sent_at")
    ordering = ("-sent_at",)

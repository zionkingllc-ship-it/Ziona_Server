from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from core.users.models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Admin configuration for the custom User model."""

    list_display = [
        "email",
        "username",
        "role",
        "is_email_verified",
        "auth_provider",
        "is_active",
        "created_at",
    ]
    list_filter = ["role", "is_email_verified", "auth_provider", "is_active"]
    search_fields = ["email", "username", "full_name"]
    ordering = ["-created_at"]

    fieldsets = (
        (None, {"fields": ("email", "username", "password")}),
        (
            "Personal Info",
            {"fields": ("full_name", "bio", "avatar_url", "location")},
        ),
        (
            "Auth & Roles",
            {
                "fields": (
                    "role",
                    "is_email_verified",
                    "auth_provider",
                    "firebase_uid",
                ),
            },
        ),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                ),
            },
        ),
        ("Security", {"fields": ("last_login_ip", "last_login")}),
    )

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "username", "password1", "password2", "role"),
            },
        ),
    )

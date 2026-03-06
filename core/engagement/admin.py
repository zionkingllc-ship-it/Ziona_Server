from django.contrib import admin

from core.engagement.models import (
    BookmarkFolder,
    Comment,
    CommentLike,
    Like,
    Save,
    Share,
)


@admin.register(Like)
class LikeAdmin(admin.ModelAdmin):
    """Admin configuration for Likes."""

    list_display = ("id", "user", "post", "created_at")
    raw_id_fields = ("user", "post")


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    """Admin configuration for Comments."""

    list_display = ("id", "user", "post", "text", "parent_comment", "created_at")
    list_filter = ("deleted_at",)
    search_fields = ("text",)
    raw_id_fields = ("user", "post", "parent_comment")


@admin.register(CommentLike)
class CommentLikeAdmin(admin.ModelAdmin):
    """Admin configuration for CommentLikes."""

    list_display = ("id", "user", "comment", "created_at")
    raw_id_fields = ("user", "comment")


@admin.register(BookmarkFolder)
class BookmarkFolderAdmin(admin.ModelAdmin):
    """Admin configuration for BookmarkFolders."""

    list_display = ("id", "user", "name", "created_at")
    raw_id_fields = ("user",)


@admin.register(Save)
class SaveAdmin(admin.ModelAdmin):
    """Admin configuration for Saves."""

    list_display = ("id", "user", "post", "folder", "created_at")
    raw_id_fields = ("user", "post", "folder")


@admin.register(Share)
class ShareAdmin(admin.ModelAdmin):
    """Admin configuration for Shares."""

    list_display = ("id", "user", "post", "share_type", "recipient", "created_at")
    list_filter = ("share_type",)
    raw_id_fields = ("user", "post", "recipient")

"""
Post models for the Ziona platform.

Defines the Post and PostMedia models for content creation —
supporting image, video, and text post types.
"""

from django.db import models

from core.shared.models import (
    ActiveUserContentManager,
    AllObjectsManager,
    SoftDeleteModel,
    TimestampedModel,
)


class PostType(models.TextChoices):
    """Supported post content types."""

    IMAGE = "image", "Image"
    VIDEO = "video", "Video"
    TEXT = "text", "Text"


class PostCategory(models.TextChoices):
    """Faith-based content categories."""

    LOVE = "love", "Love"
    TRUST = "trust", "Trust"
    WORSHIP = "worship", "Worship"
    PATIENCE = "patience", "Patience"
    PRAYER = "prayer", "Prayer"


class Post(SoftDeleteModel):
    """A user-generated content post.

    Supports three types: image (1-10 images), video (single 60-80s clip),
    and text (caption-only with optional background).

    Attributes:
        user: The author of the post.
        post_type: Content type (image, video, text).
        caption: Post text content (required for text, optional for others).
        category: Faith-based content category for discovery.
        media_count: Number of attached media items.
        view_count: Total view count (incremented asynchronously).
        is_mature_content: Flag for content moderation.
    """

    user = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="posts",
    )
    post_type = models.CharField(
        max_length=20,
        choices=PostType.choices,
        db_index=True,
    )
    caption = models.TextField(max_length=2200, blank=True, null=True)
    category = models.ForeignKey(
        "categories.Category",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        db_index=True,
    )
    media_count = models.IntegerField(default=0)
    view_count = models.IntegerField(default=0)
    is_mature_content = models.BooleanField(default=False)
    media_files = models.ManyToManyField(
        "media.MediaFile",
        related_name="posts",
        blank=True,
        help_text="Attached media files via uploadMedia",
    )

    scripture_book = models.CharField(max_length=50, blank=True, null=True)
    scripture_chapter = models.IntegerField(blank=True, null=True)
    scripture_verse_start = models.IntegerField(blank=True, null=True)
    scripture_verse_end = models.IntegerField(blank=True, null=True)
    scripture_translation = models.CharField(max_length=10, default="KJV")

    objects = ActiveUserContentManager()
    all_objects = AllObjectsManager()

    class Meta:
        db_table = "posts"
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["user", "-created_at"],
                name="idx_post_user_created",
            ),
            models.Index(fields=["category"], name="idx_post_category"),
            models.Index(fields=["deleted_at"], name="idx_post_deleted"),
        ]

    def __str__(self) -> str:
        """Return string representation."""
        return f"Post({self.post_type}) by {self.user_id} — {self.id}"


class PostMedia(TimestampedModel):
    """Media attachment for a Post.

    Each post can have one or more media items. For image posts,
    multiple items are ordered by the `order` field. Video posts
    have exactly one media item.

    Attributes:
        post: Parent post this media belongs to.
        media_url: Signed URL or storage path to the media file.
        media_type: Type of media file (image or video).
        thumbnail_url: URL for video thumbnail or image preview.
        order: Display order within the post (0-indexed).
        width: Media width in pixels.
        height: Media height in pixels.
        duration: Video duration in seconds (null for images).
    """

    post = models.ForeignKey(
        Post,
        on_delete=models.CASCADE,
        related_name="post_media",
    )
    media_url = models.URLField(max_length=500)
    media_type = models.CharField(
        max_length=20,
        choices=[("image", "Image"), ("video", "Video")],
    )
    thumbnail_url = models.URLField(max_length=500, blank=True, null=True)
    order = models.IntegerField(default=0)
    width = models.IntegerField(default=0)
    height = models.IntegerField(default=0)
    duration = models.IntegerField(
        null=True,
        blank=True,
        help_text="Video duration in seconds",
    )

    class Meta:
        db_table = "post_media"
        ordering = ["order"]
        constraints = [
            models.UniqueConstraint(
                fields=["post", "order"],
                name="uq_postmedia_post_order",
            ),
        ]
        indexes = [
            models.Index(fields=["post", "order"], name="idx_postmedia_post_order"),
        ]

    def __str__(self) -> str:
        """Return string representation."""
        return f"PostMedia({self.media_type}) #{self.order} for {self.post_id}"

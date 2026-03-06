"""
Engagement models for the Ziona platform.

Defines Like, Comment, CommentLike, Save, BookmarkFolder, and Share models
for all post interaction mechanics.
"""

from django.db import models

from core.shared.models import SoftDeleteModel, TimestampedModel


class Like(TimestampedModel):
    """A like on a post.

    Attributes:
        user: The user who liked the post.
        post: The post that was liked.
    """

    user = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="likes",
    )
    post = models.ForeignKey(
        "posts.Post",
        on_delete=models.CASCADE,
        related_name="likes",
    )

    class Meta:
        db_table = "likes"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "post"],
                name="uq_like_user_post",
            ),
        ]
        indexes = [
            models.Index(fields=["post"], name="idx_like_post"),
            models.Index(fields=["user", "post"], name="idx_like_user_post"),
        ]

    def __str__(self) -> str:
        """Return string representation."""
        return f"Like by {self.user_id} on {self.post_id}"


class Comment(SoftDeleteModel):
    """A comment on a post, supporting threaded replies.

    Threading is limited to 3 levels deep (enforced in service layer).

    Attributes:
        post: The post being commented on.
        user: The comment author.
        parent_comment: Parent comment for threaded replies (null = top-level).
        text: Comment text content (max 500 chars).
        mentioned_users: JSON array of mentioned user IDs.
    """

    post = models.ForeignKey(
        "posts.Post",
        on_delete=models.CASCADE,
        related_name="comments",
    )
    user = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="comments",
    )
    parent_comment = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="replies",
    )
    text = models.TextField(max_length=500)
    mentioned_users = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = "comments"
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["post", "-created_at"],
                name="idx_comment_post_created",
            ),
            models.Index(fields=["parent_comment"], name="idx_comment_parent"),
            models.Index(fields=["user"], name="idx_comment_user"),
        ]

    def __str__(self) -> str:
        """Return string representation."""
        return f"Comment by {self.user_id} on {self.post_id}"


class CommentLike(TimestampedModel):
    """A like on a comment.

    Attributes:
        user: The user who liked the comment.
        comment: The comment that was liked.
    """

    user = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="comment_likes",
    )
    comment = models.ForeignKey(
        Comment,
        on_delete=models.CASCADE,
        related_name="comment_likes",
    )

    class Meta:
        db_table = "comment_likes"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "comment"],
                name="uq_commentlike_user_comment",
            ),
        ]
        indexes = [
            models.Index(fields=["comment"], name="idx_commentlike_comment"),
            models.Index(
                fields=["user", "comment"],
                name="idx_commentlike_user_comment",
            ),
        ]

    def __str__(self) -> str:
        """Return string representation."""
        return f"CommentLike by {self.user_id} on {self.comment_id}"


class BookmarkFolder(TimestampedModel):
    """A folder for organizing saved posts.

    Default folders are auto-created on the user's first save:
    "All", "Churches", "Prayer References", "Bible Study", "Events/Concerts".

    Attributes:
        user: Folder owner.
        name: Folder display name.
    """

    user = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="bookmark_folders",
    )
    name = models.CharField(max_length=100)

    class Meta:
        db_table = "bookmark_folders"
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["user"], name="idx_bookmarkfolder_user"),
        ]

    def __str__(self) -> str:
        """Return string representation."""
        return f"Folder '{self.name}' for {self.user_id}"


class Save(TimestampedModel):
    """A bookmarked/saved post.

    Attributes:
        user: The user who saved the post.
        post: The post that was saved.
        folder: Optional bookmark folder (null = "All").
    """

    user = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="saves",
    )
    post = models.ForeignKey(
        "posts.Post",
        on_delete=models.CASCADE,
        related_name="saves",
    )
    folder = models.ForeignKey(
        BookmarkFolder,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="saves",
    )

    class Meta:
        db_table = "saves"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "post"],
                name="uq_save_user_post",
            ),
        ]
        indexes = [
            models.Index(
                fields=["user", "-created_at"],
                name="idx_save_user_created",
            ),
            models.Index(fields=["folder"], name="idx_save_folder"),
        ]

    def __str__(self) -> str:
        """Return string representation."""
        return f"Save by {self.user_id} on {self.post_id}"


class Share(TimestampedModel):
    """A share action on a post (internal to a user, or external link).

    Attributes:
        user: The user who shared the post.
        post: The post that was shared.
        recipient: For internal shares, the target user.
        share_type: Internal (to another Ziona user) or external (link).
    """

    user = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="shares_sent",
    )
    post = models.ForeignKey(
        "posts.Post",
        on_delete=models.CASCADE,
        related_name="shares",
    )
    recipient = models.ForeignKey(
        "users.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="shares_received",
    )
    share_type = models.CharField(
        max_length=20,
        choices=[("internal", "Internal"), ("external", "External")],
    )

    class Meta:
        db_table = "shares"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["post"], name="idx_share_post"),
            models.Index(fields=["user"], name="idx_share_user"),
            models.Index(fields=["recipient"], name="idx_share_recipient"),
        ]

    def __str__(self) -> str:
        """Return string representation."""
        return f"Share({self.share_type}) by {self.user_id} of {self.post_id}"

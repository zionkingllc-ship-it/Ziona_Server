"""
Follow model for the Ziona social graph.

Defines the follow/following relationship between users,
with a CHECK constraint preventing self-follows.
"""

from django.db import models

from core.shared.models import TimestampedModel


class Follow(TimestampedModel):
    """A directional follow relationship between two users.

    Attributes:
        follower: The user who is following.
        following: The user being followed.
    """

    follower = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="following_set",
    )
    following = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="follower_set",
    )

    class Meta:
        db_table = "follows"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["follower", "following"],
                name="uq_follow_pair",
            ),
            models.CheckConstraint(
                check=~models.Q(follower=models.F("following")),
                name="ck_follow_no_self",
            ),
        ]
        indexes = [
            models.Index(fields=["follower"], name="idx_follow_follower"),
            models.Index(fields=["following"], name="idx_follow_following"),
            models.Index(
                fields=["follower", "following"],
                name="idx_follow_pair",
            ),
        ]

    def __str__(self) -> str:
        """Return string representation."""
        return f"{self.follower_id} → {self.following_id}"

"""
Share service — business logic for sharing posts.

Handles internal (direct to another Ziona user) and external (link)
sharing, plus friends list for the share picker.
"""

import logging

from core.engagement.models import Share
from core.posts.models import Post
from core.shared.dtos import AuthorDTO, ShareResponseDTO
from core.shared.exceptions import ErrorCode, ShareError

logger = logging.getLogger("core.engagement")


class ShareService:
    """Service handling post sharing operations."""

    @staticmethod
    def share_post_direct(
        user_id: str,
        post_id: str,
        recipient_id: str,
    ) -> ShareResponseDTO:
        """Share a post directly to another user.

        Args:
            user_id: UUID of the sharing user.
            post_id: UUID of the post to share.
            recipient_id: UUID of the recipient.

        Returns:
            ShareResponseDTO with success status.

        Raises:
            ShareError: If post or recipient not found.
        """
        from core.users.models import User

        post = Post.objects.filter(id=post_id, deleted_at__isnull=True).first()
        if not post:
            raise ShareError(
                message="Post not found.",
                code=ErrorCode.POST_NOT_FOUND,
            )

        recipient = User.objects.filter(id=recipient_id, deleted_at__isnull=True).first()
        if not recipient:
            raise ShareError(
                message="Recipient not found.",
                code=ErrorCode.RECIPIENT_NOT_FOUND,
            )

        share = Share.objects.create(
            user_id=user_id,
            post_id=post_id,
            recipient_id=recipient_id,
            share_type="internal",
        )

        logger.info(
            "post_shared_direct",
            extra={
                "user_id": user_id,
                "post_id": post_id,
                "recipient_id": recipient_id,
            },
        )

        return ShareResponseDTO(
            success=True,
            share_id=str(share.id),
            share_type="internal",
        )

    @staticmethod
    def share_post_external(
        user_id: str,
        post_id: str,
    ) -> ShareResponseDTO:
        """Record an external share (deep link generation).

        Args:
            user_id: UUID of the sharing user.
            post_id: UUID of the post.

        Returns:
            ShareResponseDTO with share URL.

        Raises:
            ShareError: If post not found.
        """
        post = Post.objects.filter(id=post_id, deleted_at__isnull=True).first()
        if not post:
            raise ShareError(
                message="Post not found.",
                code=ErrorCode.POST_NOT_FOUND,
            )

        share = Share.objects.create(
            user_id=user_id,
            post_id=post_id,
            share_type="external",
        )

        share_url = f"https://ziona.app/post/{post_id}"

        logger.info(
            "post_shared_external",
            extra={"user_id": user_id, "post_id": post_id},
        )

        return ShareResponseDTO(
            success=True,
            share_id=str(share.id),
            share_type="external",
            share_url=share_url,
        )

    @staticmethod
    def get_friends_list(
        user_id: str,
        search: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Get a user's friends (mutual follows) for the share picker.

        Args:
            user_id: UUID of the user.
            search: Optional search query to filter by username.
            limit: Maximum number of results.

        Returns:
            List of dicts with user info.
        """
        from core.follows.models import Follow
        from core.users.models import User

        following_ids = set(
            Follow.objects.filter(follower_id=user_id).values_list("following_id", flat=True)
        )
        follower_ids = set(
            Follow.objects.filter(following_id=user_id).values_list("follower_id", flat=True)
        )
        mutual_ids = following_ids & follower_ids

        if not mutual_ids:
            return []

        qs = User.objects.filter(id__in=mutual_ids, deleted_at__isnull=True).order_by("username")

        if search:
            qs = qs.filter(username__icontains=search)

        return [
            {
                "user": AuthorDTO(
                    id=str(u.id),
                    username=u.username or "",
                    avatar_url=u.avatar_url or None,
                ),
                "full_name": u.full_name or "",
            }
            for u in qs[:limit]
        ]

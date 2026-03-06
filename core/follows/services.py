"""
Follow service — business logic for following and unfollowing users.

Handles self-follow prevention, cache invalidation,
and interest-based creator suggestions.
"""

import logging

from django.db import IntegrityError
from django.db.models import Count, Exists, OuterRef

from core.follows.models import Follow
from core.shared.decorators import rate_limit
from core.shared.dtos import AuthorDTO, FollowResponseDTO
from core.shared.exceptions import ErrorCode, FollowError

logger = logging.getLogger("core.follows")


class FollowService:
    """Service handling follow/unfollow and social graph operations."""

    @staticmethod
    @rate_limit(max_requests=30, window_seconds=60)
    def follow_user(follower_id: str, following_id: str) -> FollowResponseDTO:
        """Follow a user.

        Args:
            follower_id: UUID of the user who wants to follow.
            following_id: UUID of the user to follow.

        Returns:
            FollowResponseDTO with success status.

        Raises:
            FollowError: If self-follow or already following.
        """
        from core.users.models import User

        if str(follower_id) == str(following_id):
            raise FollowError(
                message="You cannot follow yourself.",
                code=ErrorCode.CANNOT_FOLLOW_SELF,
            )

        target = User.objects.filter(id=following_id, deleted_at__isnull=True).first()
        if not target:
            raise FollowError(
                message="User not found.",
                code=ErrorCode.USER_NOT_FOUND,
            )

        try:
            Follow.objects.create(
                follower_id=follower_id,
                following_id=following_id,
            )
            logger.info(
                "user_followed",
                extra={
                    "follower_id": follower_id,
                    "following_id": following_id,
                },
            )

            FollowService._invalidate_follow_cache(follower_id, following_id)

            return FollowResponseDTO(success=True, following=True)
        except IntegrityError as e:
            raise FollowError(
                message="You are already following this user.",
                code=ErrorCode.ALREADY_FOLLOWING,
            ) from e

    @staticmethod
    def unfollow_user(follower_id: str, following_id: str) -> FollowResponseDTO:
        """Unfollow a user.

        Args:
            follower_id: UUID of the follower.
            following_id: UUID of the user to unfollow.

        Returns:
            FollowResponseDTO with success status.
        """
        deleted_count, _ = Follow.objects.filter(
            follower_id=follower_id,
            following_id=following_id,
        ).delete()

        if deleted_count:
            logger.info(
                "user_unfollowed",
                extra={
                    "follower_id": follower_id,
                    "following_id": following_id,
                },
            )
            FollowService._invalidate_follow_cache(follower_id, following_id)

        return FollowResponseDTO(success=True, following=False)

    @staticmethod
    def get_followers(
        user_id: str,
        viewer_id: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> dict:
        """Get paginated list of followers for a user.

        Args:
            user_id: UUID of the user whose followers to fetch.
            viewer_id: Optional viewer for mutual follow status.
            cursor: Cursor for pagination.
            limit: Page size.

        Returns:
            Dict with users, next_cursor, has_more.
        """
        limit = min(limit, 50)

        qs = (
            Follow.objects.select_related("follower")
            .filter(following_id=user_id)
            .order_by("-created_at")
        )

        if cursor:
            try:
                cursor_follow = Follow.objects.filter(id=cursor).values("created_at").first()
                if cursor_follow:
                    qs = qs.filter(created_at__lt=cursor_follow["created_at"])
            except Exception:  # noqa: S110
                pass

        follows = list(qs[: limit + 1])
        has_more = len(follows) > limit
        follows = follows[:limit]

        mutual_ids = set()
        if viewer_id:
            follower_ids = [str(f.follower_id) for f in follows]
            mutual_ids = set(
                Follow.objects.filter(
                    follower_id=viewer_id,
                    following_id__in=follower_ids,
                ).values_list("following_id", flat=True)
            )

        users = []
        for f in follows:
            user = f.follower
            users.append(
                {
                    "user": AuthorDTO(
                        id=str(user.id),
                        username=user.username or "",
                        avatar_url=user.avatar_url or None,
                    ),
                    "is_following": user.id in mutual_ids,
                }
            )

        return {
            "users": users,
            "next_cursor": str(follows[-1].id) if has_more and follows else None,
            "has_more": has_more,
        }

    @staticmethod
    def get_following(
        user_id: str,
        viewer_id: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> dict:
        """Get paginated list of users that a user follows.

        Args:
            user_id: UUID of the user.
            viewer_id: Optional viewer for mutual follow status.
            cursor: Cursor for pagination.
            limit: Page size.

        Returns:
            Dict with users, next_cursor, has_more.
        """
        limit = min(limit, 50)

        qs = (
            Follow.objects.select_related("following")
            .filter(follower_id=user_id)
            .order_by("-created_at")
        )

        if cursor:
            try:
                cursor_follow = Follow.objects.filter(id=cursor).values("created_at").first()
                if cursor_follow:
                    qs = qs.filter(created_at__lt=cursor_follow["created_at"])
            except Exception:  # noqa: S110
                pass

        follows = list(qs[: limit + 1])
        has_more = len(follows) > limit
        follows = follows[:limit]

        mutual_ids = set()
        if viewer_id and str(viewer_id) != str(user_id):
            following_ids = [str(f.following_id) for f in follows]
            mutual_ids = set(
                Follow.objects.filter(
                    follower_id=viewer_id,
                    following_id__in=following_ids,
                ).values_list("following_id", flat=True)
            )

        users = []
        for f in follows:
            user = f.following
            users.append(
                {
                    "user": AuthorDTO(
                        id=str(user.id),
                        username=user.username or "",
                        avatar_url=user.avatar_url or None,
                    ),
                    "is_following": (
                        True if str(viewer_id) == str(user_id) else user.id in mutual_ids
                    ),
                }
            )

        return {
            "users": users,
            "next_cursor": str(follows[-1].id) if has_more and follows else None,
            "has_more": has_more,
        }

    @staticmethod
    def get_suggested_creators(
        user_id: str,
        limit: int = 10,
    ) -> list[dict]:
        """Get suggested creators based on user interests.

        Orders by follower count and filters out already-followed users.

        Args:
            user_id: UUID of the requesting user.
            limit: Number of suggestions.

        Returns:
            List of dicts with user info and follower_count.
        """
        from core.users.models import User, UserInterest

        user_interests = list(
            UserInterest.objects.filter(user_id=user_id).values_list("interest", flat=True)
        )

        following_ids = set(
            Follow.objects.filter(follower_id=user_id).values_list("following_id", flat=True)
        )
        following_ids.add(user_id)

        qs = (
            User.objects.filter(deleted_at__isnull=True)
            .exclude(id__in=following_ids)
            .annotate(
                followers_count=Count("follower_set", distinct=True),
                posts_count=Count("posts", distinct=True),
            )
            .filter(posts_count__gt=0)
            .order_by("-followers_count")
        )

        if user_interests:
            qs = qs.annotate(
                has_matching_interest=Exists(
                    UserInterest.objects.filter(
                        user_id=OuterRef("id"),
                        interest__in=user_interests,
                    )
                )
            ).order_by("-has_matching_interest", "-followers_count")

        suggestions = []
        for user in qs[:limit]:
            suggestions.append(
                {
                    "user": AuthorDTO(
                        id=str(user.id),
                        username=user.username or "",
                        avatar_url=user.avatar_url or None,
                    ),
                    "bio": user.bio or None,
                    "followers_count": user.followers_count,
                }
            )

        return suggestions

    @staticmethod
    def _invalidate_follow_cache(follower_id: str, following_id: str) -> None:
        """Invalidate cached follow data."""
        try:
            from django.core.cache import cache

            cache.delete_many(
                [
                    f"followers:{following_id}",
                    f"following:{follower_id}",
                    f"is_following:{follower_id}:{following_id}",
                ]
            )
        except Exception:
            logger.warning("Follow cache invalidation failed")

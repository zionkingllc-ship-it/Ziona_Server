"""
Profile service — business logic for user profiles.

Handles profile retrieval with stats and viewer context,
and profile updates with validation.
"""

import logging

from django.db.models import Count, Q

from core.follows.selectors import FollowSelector
from core.posts.models import Post
from core.shared.dtos import (
    UserProfileDTO,
    UserProfileStatsDTO,
)
from core.shared.exceptions import ErrorCode, ProfileError

logger = logging.getLogger("core.profiles")

BIO_MAX_LENGTH = 150
DISPLAY_NAME_MAX_LENGTH = 150


class ProfileService:
    """Service handling user profile operations."""

    @staticmethod
    def get_user_profile(
        target_user_id: str,
        viewer_id: str | None = None,
    ) -> UserProfileDTO:
        """Get a user's profile with stats and viewer context.

        Args:
            target_user_id: UUID of the profile to view.
            viewer_id: UUID of the viewing user (for follow state).

        Returns:
            UserProfileDTO with stats and recent posts.

        Raises:
            ProfileError: If user not found.
        """
        from core.users.models import User

        user = User.objects.filter(id=target_user_id, deleted_at__isnull=True).first()

        if not user:
            raise ProfileError(
                message="User not found.",
                code=ErrorCode.USER_NOT_FOUND,
            )

        follower_count = FollowSelector.get_follower_count(target_user_id)
        following_count = FollowSelector.get_following_count(target_user_id)
        posts_count = Post.objects.filter(user_id=target_user_id, deleted_at__isnull=True).count()

        stats = UserProfileStatsDTO(
            followers_count=follower_count,
            following_count=following_count,
            posts_count=posts_count,
        )

        is_following = False
        is_own_profile = False
        if viewer_id:
            is_own_profile = str(viewer_id) == str(target_user_id)
            if not is_own_profile:
                is_following = FollowSelector.is_following(viewer_id, target_user_id)

        recent_posts = (
            Post.objects.select_related("user")
            .prefetch_related("post_media")
            .filter(user_id=target_user_id, deleted_at__isnull=True)
            .annotate(
                likes_count=Count("likes", distinct=True),
                comments_count=Count(
                    "comments",
                    filter=Q(comments__deleted_at__isnull=True),
                    distinct=True,
                ),
                shares_count=Count("shares", distinct=True),
                saves_count=Count("saves", distinct=True),
            )
            .order_by("-created_at")[:6]
        )

        from core.posts.services import PostService

        post_dtos = [
            PostService._build_post_dto(
                post=p,
                media_items=list(p.post_media.all()),
                viewer_id=viewer_id,
            )
            for p in recent_posts
        ]

        return UserProfileDTO(
            id=str(user.id),
            username=user.username or "",
            full_name=user.full_name or "",
            bio=user.bio or "",
            avatar_url=user.avatar_url or None,
            location=user.location or "",
            stats=stats,
            is_following=is_following,
            is_own_profile=is_own_profile,
            recent_posts=post_dtos,
            created_at=user.created_at.isoformat(),
        )

    @staticmethod
    def update_profile(
        user_id: str,
        bio: str | None = None,
        full_name: str | None = None,
        avatar_url: str | None = None,
        location: str | None = None,
    ) -> UserProfileDTO:
        """Update a user's profile.

        Args:
            user_id: UUID of the user.
            bio: New bio text (max 150 chars).
            full_name: New display name.
            avatar_url: New avatar URL.
            location: New location string.

        Returns:
            Updated UserProfileDTO.

        Raises:
            ProfileError: If validation fails.
        """
        from core.users.models import User

        user = User.objects.filter(id=user_id, deleted_at__isnull=True).first()

        if not user:
            raise ProfileError(
                message="User not found.",
                code=ErrorCode.USER_NOT_FOUND,
            )

        update_fields = ["updated_at"]

        if bio is not None:
            if len(bio) > BIO_MAX_LENGTH:
                raise ProfileError(
                    message=f"Bio must be {BIO_MAX_LENGTH} characters or fewer.",
                    code=ErrorCode.VALIDATION_ERROR,
                )
            user.bio = bio
            update_fields.append("bio")

        if full_name is not None:
            if len(full_name) > DISPLAY_NAME_MAX_LENGTH:
                raise ProfileError(
                    message=f"Display name must be {DISPLAY_NAME_MAX_LENGTH} characters or fewer.",
                    code=ErrorCode.VALIDATION_ERROR,
                )
            user.full_name = full_name
            update_fields.append("full_name")

        if avatar_url is not None:
            user.avatar_url = avatar_url
            update_fields.append("avatar_url")

        if location is not None:
            user.location = location
            update_fields.append("location")

        if len(update_fields) > 1:
            user.save(update_fields=update_fields)

            from django.core.cache import cache

            cache.delete(f"user_me_data_{user_id}")

            logger.info(
                "profile_updated",
                extra={"user_id": user_id, "fields": update_fields},
            )

        return ProfileService.get_user_profile(user_id, viewer_id=user_id)

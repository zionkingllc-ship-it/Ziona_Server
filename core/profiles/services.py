"""
Profile service — business logic for user profiles.

Handles profile retrieval with stats and viewer context,
and profile updates with validation.
"""

import logging
import re

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
AVATAR_URL_MAX_LENGTH = 500

# Only allow public http/https URLs — rejects local device paths (file://, content://)
_AVATAR_URL_PATTERN = re.compile(
    r"^https?://"
    r"(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|"  # domain
    r"localhost|"  # localhost
    r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})"  # IPv4
    r"(?::\d+)?(?:/?|[/?]\S+)$",
    re.IGNORECASE,
)


class ProfileService:
    """Service handling user profile operations."""

    @staticmethod
    def _validate_avatar_url(url: str) -> None:
        """Validate that an avatar URL is a publicly accessible http/https URL.

        Rejects local device paths (file://, content://) that a mobile client
        might accidentally pass before completing the GCP upload step.

        Args:
            url: The avatar URL string to validate.

        Raises:
            ProfileError: If the URL is invalid or not a public http/https URL.
        """
        if len(url) > AVATAR_URL_MAX_LENGTH:
            raise ProfileError(
                message=f"Avatar URL must be {AVATAR_URL_MAX_LENGTH} characters or fewer.",
                code=ErrorCode.VALIDATION_ERROR,
            )

        if not _AVATAR_URL_PATTERN.match(url):
            # Give a developer-friendly message that explains the required flow
            raise ProfileError(
                message=(
                    "Invalid avatar URL. A public https:// URL is required. "
                    "Upload the image to cloud storage first (via uploadMedia), "
                    "then pass the returned URL here."
                ),
                code=ErrorCode.VALIDATION_ERROR,
            )

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
        is_followed_by = False
        is_own_profile = False
        if viewer_id:
            is_own_profile = str(viewer_id) == str(target_user_id)
            if not is_own_profile:
                is_following = FollowSelector.is_following(viewer_id, target_user_id)
                is_followed_by = FollowSelector.is_following(target_user_id, viewer_id)

        recent_posts = (
            Post.objects.select_related("user")
            # Use media_files (M2M) — the relation that create_post writes to.
            # The legacy post_media (FK reverse) is never populated by the current
            # post creation flow and must NOT be used here.
            .prefetch_related("media_files")
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
            .order_by("-created_at", "-id")[:6]
        )

        from core.feed.services import FeedService

        # Use bulk viewer state fetching (3 total DB queries) instead of
        # the individual loop (3 queries × 6 posts = 18 queries).
        post_dtos = FeedService._bulk_build_post_dtos(list(recent_posts), viewer_id=viewer_id)

        return UserProfileDTO(
            id=str(user.id),
            username=user.username or "",
            full_name=user.full_name or "",
            bio=user.bio or "",
            avatar_url=user.avatar_url or None,
            location=user.location or "",
            hide_like_count=getattr(user, "hide_like_count", False),
            stats=stats,
            is_following=is_following,
            is_followed_by=is_followed_by,
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
        hide_like_count: bool | None = None,
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

        if full_name is not None and full_name != user.full_name:
            if len(full_name) > DISPLAY_NAME_MAX_LENGTH:
                raise ProfileError(
                    message=f"Display name must be {DISPLAY_NAME_MAX_LENGTH} characters or fewer.",
                    code=ErrorCode.VALIDATION_ERROR,
                )

            # 14-day limit check
            if user.last_name_change:
                from datetime import timedelta

                from django.utils import timezone

                days_since_change = (timezone.now() - user.last_name_change).days
                if days_since_change < 14:
                    next_change = user.last_name_change + timedelta(days=14)
                    raise ProfileError(
                        message=f"You're allowed one name change every 14 days. Next change on {next_change.strftime('%B %d, %Y')}.",
                        code=ErrorCode.VALIDATION_ERROR,
                    )

            from django.utils import timezone

            user.full_name = full_name
            user.last_name_change = timezone.now()
            update_fields.append("full_name")
            update_fields.append("last_name_change")

        if avatar_url is not None:
            ProfileService._validate_avatar_url(avatar_url)
            user.avatar_url = avatar_url
            update_fields.append("avatar_url")

        if location is not None:
            user.location = location
            update_fields.append("location")

        if hide_like_count is not None:
            user.hide_like_count = hide_like_count
            update_fields.append("hide_like_count")

        if len(update_fields) > 1:
            user.save(update_fields=update_fields)

            from django.core.cache import cache

            cache.delete(f"user_me_data_{user_id}")

            logger.info(
                "profile_updated",
                extra={"user_id": user_id, "fields": update_fields},
            )

        return ProfileService.get_user_profile(user_id, viewer_id=user_id)

    @staticmethod
    def get_user_posts(
        user_id: str,
        limit: int = 20,
        cursor: str | None = None,
        viewer_id: str | None = None,
    ) -> dict:
        """Get a paginated list of posts authored by the user.

        Args:
            user_id: UUID of the post author.
            limit: Page size limit.
            cursor: Pagination string.
            viewer_id: UUID of the requesting user.

        Returns:
            Dict containing 'posts', 'next_cursor', 'has_more'.
        """
        from core.feed.services import FeedService
        from core.posts.models import Post

        limit = min(limit, 50)

        qs = (
            Post.objects.select_related("user")
            .prefetch_related("media_files", "post_media")
            .filter(user_id=user_id, deleted_at__isnull=True)
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
            # -id tiebreaker ensures deterministic keyset pagination when
            # two posts share the same created_at timestamp.
            .order_by("-created_at", "-id")
        )

        if cursor:
            qs = FeedService._apply_cursor(qs, cursor)

        posts = list(qs[: limit + 1])
        has_more = len(posts) > limit
        posts = posts[:limit]

        post_dtos = FeedService._bulk_build_post_dtos(posts, viewer_id=viewer_id)

        return {
            "posts": post_dtos,
            "next_cursor": str(posts[-1].id) if has_more and posts else None,
            "has_more": has_more,
        }

    @staticmethod
    def get_user_liked_posts(
        user_id: str,
        limit: int = 20,
        cursor: str | None = None,
        viewer_id: str | None = None,
    ) -> dict:
        """Get a paginated list of posts the user has liked.

        Args:
            user_id: UUID of the user who liked the posts.
            limit: Page size limit.
            cursor: Pagination string.
            viewer_id: UUID of the requesting user.

        Returns:
            Dict containing 'posts', 'next_cursor', 'has_more'.
        """
        from core.engagement.models import Like
        from core.feed.services import FeedService
        from core.posts.models import Post

        limit = min(limit, 50)

        liked_post_ids = list(
            Like.objects.filter(user_id=user_id).values_list("post_id", flat=True)
        )

        if not liked_post_ids:
            return {
                "posts": [],
                "next_cursor": None,
                "has_more": False,
            }

        qs = (
            Post.objects.select_related("user")
            .prefetch_related("media_files", "post_media")
            .filter(id__in=liked_post_ids, deleted_at__isnull=True)
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
            # -id tiebreaker ensures deterministic keyset pagination.
            .order_by("-created_at", "-id")
        )

        if cursor:
            qs = FeedService._apply_cursor(qs, cursor)

        posts = list(qs[: limit + 1])
        has_more = len(posts) > limit
        posts = posts[:limit]

        post_dtos = FeedService._bulk_build_post_dtos(posts, viewer_id=viewer_id)

        return {
            "posts": post_dtos,
            "next_cursor": str(posts[-1].id) if has_more and posts else None,
            "has_more": has_more,
        }

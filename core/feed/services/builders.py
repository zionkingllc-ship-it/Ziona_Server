"""Feed DTO builders — bulk post DTO assembly + empty-state suggestions.

Split from the former core/feed/services.py (no behavior change).
"""

import logging

from core.shared.dtos import (
    UserSuggestionDTO,
)

logger = logging.getLogger("core.feed")


def _get_empty_state_suggestions(
    user_id: str,
    limit: int = 5,
) -> list[UserSuggestionDTO]:
    """Get user suggestions for empty feed states."""
    from core.follows.services import FollowService

    suggestions_data = FollowService.get_suggested_creators(user_id, limit=limit)

    return [
        UserSuggestionDTO(
            id=s["user"].id,
            username=s["user"].username,
            avatar_url=s["user"].avatar_url,
            bio=s.get("bio"),
            followers_count=s.get("followers_count", 0),
        )
        for s in suggestions_data
    ]


def _bulk_build_post_dtos(
    posts: list,
    viewer_id: str | None = None,
) -> list:
    """Build PostResponseDTOs for a list of posts with bulk viewer state fetching.

    Instead of 3 queries per post (liked/saved/following), this method
    fetches all viewer state data in just 3 total queries.

    Args:
        posts: List of Post instances (annotated with counts).
        viewer_id: Optional viewer user ID.

    Returns:
        List of PostResponseDTO instances.
    """
    from core.posts.services import PostService

    if not posts:
        return []

    post_ids = [str(p.id) for p in posts]
    author_ids = list({str(p.user_id) for p in posts})

    liked_post_ids: set = set()
    saved_post_ids: set = set()
    following_user_ids: set = set()
    followed_by_user_ids: set = set()

    if viewer_id:
        from core.engagement.models import Like, Save
        from core.follows.models import Follow

        liked_post_ids = set(
            Like.objects.filter(user_id=viewer_id, post_id__in=post_ids).values_list(
                "post_id", flat=True
            )
        )
        # Convert UUIDs to strings for set lookup
        liked_post_ids = {str(pid) for pid in liked_post_ids}

        saved_post_ids = set(
            Save.objects.filter(user_id=viewer_id, post_id__in=post_ids).values_list(
                "post_id", flat=True
            )
        )
        saved_post_ids = {str(pid) for pid in saved_post_ids}

        following_user_ids = set(
            Follow.objects.filter(follower_id=viewer_id, following_id__in=author_ids).values_list(
                "following_id", flat=True
            )
        )
        following_user_ids = {str(uid) for uid in following_user_ids}

        followed_by_user_ids = set(
            Follow.objects.filter(follower_id__in=author_ids, following_id=viewer_id).values_list(
                "follower_id", flat=True
            )
        )
        followed_by_user_ids = {str(uid) for uid in followed_by_user_ids}

    return [
        PostService._build_post_dto(
            post=p,
            media_items=list(p.media_files.all()),
            viewer_id=viewer_id,
            liked_post_ids=liked_post_ids,
            saved_post_ids=saved_post_ids,
            following_user_ids=following_user_ids,
            followed_by_user_ids=followed_by_user_ids,
        )
        for p in posts
    ]

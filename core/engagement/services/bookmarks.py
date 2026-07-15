"""Engagement — bookmarks operations.

Split from the former core/engagement/services.py (no behavior change).
"""
import logging
import re

from django.db import IntegrityError

from core.engagement.models import (
    BookmarkFolder,
    Save,
)
from core.posts.models import Post
from core.shared.decorators import rate_limit
from core.shared.dtos import (
    SaveResponseDTO,
)
from core.shared.exceptions import EngagementError, ErrorCode

logger = logging.getLogger("core.engagement")

COMMENT_MAX_LENGTH = 500
COMMENT_MAX_THREAD_DEPTH = 3
MENTION_REGEX = re.compile(r"@(\w{3,30})")

DEFAULT_BOOKMARK_FOLDERS = [
    "All",
]


@rate_limit(max_requests=30, window_seconds=60)
def save_post(
    user_id: str,
    post_id: str,
    folder_id: str | None = None,
    folder_name: str | None = None,
) -> SaveResponseDTO:
    """Save/bookmark a post.

    Auto-creates default folders if the user doesn't have any yet.

    Args:
        user_id: UUID of the user.
        post_id: UUID of the post.
        folder_id: Optional folder to save into.

    Returns:
        SaveResponseDTO with success status.

    Raises:
        EngagementError: If post not found or already saved.
    """
    post = Post.objects.filter(id=post_id, deleted_at__isnull=True).first()
    if not post:
        raise EngagementError(
            message="Post not found.",
            code=ErrorCode.POST_NOT_FOUND,
        )

    _ensure_default_folders(user_id)

    folder = None
    bookmark_service = None
    if folder_name and not folder_id:
        from core.engagement.bookmark_services import BookmarkService

        bookmark_service = BookmarkService
        folder = BookmarkService._get_or_create_folder_record(user_id, folder_name)
    elif folder_id:
        folder = BookmarkFolder.objects.filter(id=folder_id, user_id=user_id).first()
        if not folder:
            raise EngagementError(
                message="Bookmark folder not found.",
                code=ErrorCode.FOLDER_NOT_FOUND,
            )

    try:
        Save.objects.create(
            user_id=user_id,
            post_id=post_id,
            folder=folder,
        )
        logger.info(
            "post_saved",
            extra={"user_id": user_id, "post_id": post_id},
        )
        # Rehydrate Full Pointers correctly
        folder_dto = None
        if folder:
            if bookmark_service is None:
                from core.engagement.bookmark_services import BookmarkService

                bookmark_service = BookmarkService
            bookmark_service.seed_folder_thumbnail(folder, post)
            folder_count = Save.objects.filter(folder_id=folder.id).count()
            folder.refresh_from_db(fields=["thumbnail_url", "updated_at"])
            folder_dto = bookmark_service._build_folder_dto(folder, saved_count=folder_count)

        from core.posts.services import PostService

        post_dto = PostService._build_post_dto(
            post=post, media_items=list(post.media_files.all()), viewer_id=user_id
        )

        return SaveResponseDTO(success=True, saved=True, folder=folder_dto, post=post_dto)
    except IntegrityError as e:
        raise EngagementError(
            message="You have already saved this post.",
            code=ErrorCode.ALREADY_SAVED,
        ) from e


def unsave_post(user_id: str, post_id: str) -> SaveResponseDTO:
    """Remove a saved post.

    Args:
        user_id: UUID of the user.
        post_id: UUID of the post.

    Returns:
        SaveResponseDTO with success status.
    """
    deleted_count, _ = Save.objects.filter(user_id=user_id, post_id=post_id).delete()

    if deleted_count:
        logger.info(
            "post_unsaved",
            extra={"user_id": user_id, "post_id": post_id},
        )

    return SaveResponseDTO(success=True, saved=False)


def _ensure_default_folders(user_id: str) -> None:
    """Create default bookmark folders if user has none."""
    if BookmarkFolder.objects.filter(user_id=user_id).exists():
        return

    folders = [BookmarkFolder(user_id=user_id, name=name) for name in DEFAULT_BOOKMARK_FOLDERS]
    BookmarkFolder.objects.bulk_create(folders, ignore_conflicts=True)

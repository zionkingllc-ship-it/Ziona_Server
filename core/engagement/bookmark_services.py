"""
Bookmark service — business logic for bookmark folder management and saved posts.

Handles folder CRUD and folder-filtered saved post retrieval.
"""

import logging

from core.engagement.models import BookmarkFolder, Save
from core.shared.dtos import BookmarkFolderDTO
from core.shared.exceptions import BookmarkError, ErrorCode

logger = logging.getLogger("core.engagement")

VALID_MEDIA_TYPES = {"all", "image", "video", "text"}

DEFAULT_BOOKMARK_FOLDERS = [
    "All",
    "Churches",
    "Prayer References",
    "Bible Study",
    "Events/Concerts",
]


class BookmarkService:
    """Service handling bookmark folder management and saved posts."""

    @staticmethod
    def get_folders(user_id: str) -> list[BookmarkFolderDTO]:
        """Get all bookmark folders for a user.

        Auto-creates default folders if none exist.

        Args:
            user_id: UUID of the user.

        Returns:
            List of BookmarkFolderDTO.
        """
        BookmarkService._ensure_default_folders(user_id)

        folders = BookmarkFolder.objects.filter(user_id=user_id).order_by("created_at")

        folder_dtos = []
        for folder in folders:
            saved_count = Save.objects.filter(user_id=user_id, folder=folder).count()
            folder_dtos.append(
                BookmarkFolderDTO(
                    id=str(folder.id),
                    name=folder.name,
                    saved_count=saved_count,
                )
            )

        return folder_dtos

    @staticmethod
    def create_folder(
        user_id: str,
        name: str,
    ) -> BookmarkFolderDTO:
        """Create a new bookmark folder.

        Args:
            user_id: UUID of the user.
            name: Folder name.

        Returns:
            BookmarkFolderDTO for the new folder.

        Raises:
            BookmarkError: If validation fails.
        """
        if not name or not name.strip():
            raise BookmarkError(
                message="Folder name is required.",
                code=ErrorCode.VALIDATION_ERROR,
            )

        if len(name) > 100:
            raise BookmarkError(
                message="Folder name must be 100 characters or fewer.",
                code=ErrorCode.VALIDATION_ERROR,
            )

        folder = BookmarkFolder.objects.create(
            user_id=user_id,
            name=name.strip(),
        )

        logger.info(
            "bookmark_folder_created",
            extra={"user_id": user_id, "folder_id": str(folder.id)},
        )

        return BookmarkFolderDTO(
            id=str(folder.id),
            name=folder.name,
            saved_count=0,
        )

    @staticmethod
    def delete_folder(user_id: str, folder_id: str) -> dict:
        """Delete a bookmark folder and move its posts to 'All' (folder=None).

        Args:
            user_id: UUID of the user.
            folder_id: UUID of the folder to delete.

        Returns:
            Dict with deleted=True and moved_posts_count.

        Raises:
            BookmarkError: If folder not found or access denied.
        """
        folder = BookmarkFolder.objects.filter(id=folder_id).first()

        if not folder:
            raise BookmarkError(
                message="Bookmark folder not found.",
                code=ErrorCode.FOLDER_NOT_FOUND,
            )

        if str(folder.user_id) != str(user_id):
            raise BookmarkError(
                message="You do not have permission to delete this folder.",
                code=ErrorCode.FOLDER_ACCESS_DENIED,
            )

        moved_count = Save.objects.filter(folder=folder).update(folder=None)

        folder.delete()

        logger.info(
            "bookmark_folder_deleted",
            extra={
                "user_id": user_id,
                "folder_id": folder_id,
                "moved_posts_count": moved_count,
            },
        )

        return {
            "deleted": True,
            "moved_posts_count": moved_count,
        }

    @staticmethod
    def get_saved_posts(
        user_id: str,
        folder_id: str | None = None,
        media_type: str = "all",
        cursor: str | None = None,
        limit: int = 20,
    ) -> dict:
        """Get saved posts optionally filtered by folder and media type.

        Args:
            user_id: UUID of the user.
            folder_id: Optional folder filter (None = all folders).
            media_type: Filter by post type ("all", "image", "video", "text").
            cursor: Save ID for pagination.
            limit: Page size (max 50).

        Returns:
            Dict with posts, next_cursor, has_more.

        Raises:
            BookmarkError: If media_type or folder_id is invalid.
        """
        from core.posts.services import PostService

        media_type = media_type.lower().strip() if media_type else "all"
        if media_type not in VALID_MEDIA_TYPES:
            raise BookmarkError(
                message=(
                    f"Invalid media type '{media_type}'. "
                    f"Must be one of: {', '.join(sorted(VALID_MEDIA_TYPES))}."
                ),
                code=ErrorCode.INVALID_MEDIA_TYPE,
            )

        limit = min(limit, 50)

        qs = (
            Save.objects.select_related("post", "post__user")
            .prefetch_related("post__post_media")
            .filter(user_id=user_id, post__deleted_at__isnull=True)
            .order_by("-created_at")
        )

        if folder_id:
            folder_exists = BookmarkFolder.objects.filter(id=folder_id, user_id=user_id).exists()
            if not folder_exists:
                raise BookmarkError(
                    message="Bookmark folder not found.",
                    code=ErrorCode.FOLDER_NOT_FOUND,
                )
            qs = qs.filter(folder_id=folder_id)

        if media_type != "all":
            qs = qs.filter(post__post_type=media_type)

        if cursor:
            try:
                cursor_save = Save.objects.filter(id=cursor).values("created_at").first()
                if cursor_save:
                    qs = qs.filter(created_at__lt=cursor_save["created_at"])
            except Exception:
                logger.debug("Invalid pagination cursor: %s", cursor)

        saves = list(qs[: limit + 1])
        has_more = len(saves) > limit
        saves = saves[:limit]

        post_dtos = [
            PostService._build_post_dto(
                post=s.post,
                media_items=list(s.post.post_media.all()),
                viewer_id=user_id,
            )
            for s in saves
        ]

        return {
            "posts": post_dtos,
            "next_cursor": str(saves[-1].id) if has_more and saves else None,
            "has_more": has_more,
        }

    @staticmethod
    def bulk_remove_bookmarks(user_id: str, post_ids: list[str]) -> dict:
        """Remove multiple bookmarks at once.

        Silently skips posts that aren't bookmarked by the user.

        Args:
            user_id: UUID of the user.
            post_ids: List of post UUIDs to un-bookmark.

        Returns:
            Dict with removed_count.
        """
        if not post_ids:
            return {"removed_count": 0}

        deleted_count, _ = Save.objects.filter(user_id=user_id, post_id__in=post_ids).delete()

        logger.info(
            "bulk_bookmarks_removed",
            extra={
                "user_id": user_id,
                "requested": len(post_ids),
                "removed": deleted_count,
            },
        )

        return {"removed_count": deleted_count}

    @staticmethod
    def _ensure_default_folders(user_id: str) -> None:
        """Create default bookmark folders if user has none."""
        if BookmarkFolder.objects.filter(user_id=user_id).exists():
            return

        folders = [BookmarkFolder(user_id=user_id, name=name) for name in DEFAULT_BOOKMARK_FOLDERS]
        BookmarkFolder.objects.bulk_create(folders, ignore_conflicts=True)

from __future__ import annotations

from typing import TYPE_CHECKING

import strawberry

from core.users.schema import _get_authenticated_user_id

if TYPE_CHECKING:
    from core.feed.schema import FeedPost


@strawberry.type
class MediaItemInput:
    """Input for a single media attachment."""

    url: str
    media_type: str
    thumbnail_url: str | None = None
    width: int = 0
    height: int = 0
    duration: int | None = None
    order: int = 0


@strawberry.type
class PostPayload:
    """
    Standard response wrapper for Post mutations.

    Contains the resulting `post_id` used for frontend routing on success.

    **Authentication:** Required
    **Related operations:** create_post, update_post, delete_post
    """

    success: bool = strawberry.field(description="Whether the mutation succeeded")
    post_id: str | None = strawberry.field(
        default=None, description="The UUID of the affected post"
    )
    message: str | None = strawberry.field(default=None, description="Error or success string")
    error_code: str | None = strawberry.field(
        default=None, description="Detailed failure string identifier"
    )


@strawberry.type
class PostMutations:
    """Post domain GraphQL mutations."""

    @strawberry.mutation(
        description="Create a new multimedia app post. Supports Text, Image, and Video variants with nested Scriptures."
    )
    def create_post(
        self,
        info: strawberry.types.Info,
        post_type: str,
        caption: str | None = None,
        category: str | None = None,
        media_urls: list[str] | None = None,
        media_type: str | None = None,
        thumbnail_url: str | None = None,
        width: int = 0,
        height: int = 0,
        duration: int | None = None,
        scripture_book: str | None = None,
        scripture_chapter: int | None = None,
        scripture_verse_start: int | None = None,
        scripture_verse_end: int | None = None,
        scripture_version: str | None = "KJV",
    ) -> PostPayload:
        """
        Create a new content post globally.

        This handles all 3 media configurations dynamically: Images (up to 10), Video (single 80s chunk),
        and Text (no media needed). Attaches scripture safely globally if bounded.

        **Authentication:** Required
        **Parameters:**
        - post_type (String, required) - Defines boundary rules
        - caption (String, optional) - Allowed for all types
        - media_urls ([String], optional) - Media paths mapped cleanly
        - scripture_* (Various, optional) - Formats a dynamic Bible preview natively
        **Returns:** PostPayload resolving to the inserted database `post_id`
        **Errors:** UNAUTHENTICATED, VALIDATION_ERROR native limits.
        """
        from core.posts.services import PostService
        from core.shared.exceptions import PostError

        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return PostPayload(
                success=False,
                message="Authentication required",
                error_code="UNAUTHORIZED",
            )

        try:
            media_items = None
            if media_urls:
                media_items = [
                    {
                        "media_url": url,
                        "media_type": media_type or post_type,
                        "thumbnail_url": thumbnail_url,
                        "width": width,
                        "height": height,
                        "duration": duration,
                        "order": i,
                    }
                    for i, url in enumerate(media_urls)
                ]

            result = PostService.create_post(
                user_id=user_id,
                post_type=post_type,
                caption=caption,
                media_items=media_items,
                category=category,
                scripture_book=scripture_book,
                scripture_chapter=scripture_chapter,
                scripture_verse_start=scripture_verse_start,
                scripture_verse_end=scripture_verse_end,
                scripture_version=scripture_version or "KJV",
            )

            return PostPayload(
                success=True,
                post_id=result.id,
            )
        except PostError as e:
            return PostPayload(
                success=False,
                message=e.message,
                error_code=e.code,
            )

    @strawberry.mutation(
        description="Edit the caption of an existing post. Only accessible by post owner."
    )
    def update_post(
        self,
        info: strawberry.types.Info,
        post_id: str,
        caption: str | None = None,
    ) -> PostPayload:
        """
        Update an owned post's text caption within the edit window perfectly.

        Currently restricts mutating explicit file payloads and scripture attachments
        after creation to preserve referential consistency tracking.

        **Authentication:** Required
        **Parameters:**
        - post_id (String, required) - Valid remote UUID mapping
        - caption (String, optional) - Allowed dynamic override
        **Returns:** PostPayload mapping UUID
        **Errors:** UNAUTHENTICATED, NOT_FOUND, PERMISSION_DENIED
        """
        from core.posts.services import PostService
        from core.shared.exceptions import PostError

        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return PostPayload(
                success=False,
                message="Authentication required",
                error_code="UNAUTHORIZED",
            )

        try:
            PostService.update_post(
                post_id=post_id,
                user_id=user_id,
                caption=caption,
            )
            return PostPayload(success=True, post_id=post_id)
        except PostError as e:
            return PostPayload(
                success=False,
                message=e.message,
                error_code=e.code,
            )

    @strawberry.mutation(description="Soft delete an existing post.")
    def delete_post(
        self,
        info: strawberry.types.Info,
        post_id: str,
    ) -> PostPayload:
        """
        Soft-delete a post entirely natively.

        Applies a `deleted_at` timestamp preserving the record integrity for analytics
        whilst blinding it entirely from all feed queues.

        **Authentication:** Required
        **Parameters:**
        - post_id (String, required) - Mapped target natively
        **Returns:** PostPayload confirming deletion mapping
        **Errors:** UNAUTHENTICATED, NOT_FOUND, PERMISSION_DENIED
        """
        from core.posts.services import PostService
        from core.shared.exceptions import PostError

        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return PostPayload(
                success=False,
                message="Authentication required",
                error_code="UNAUTHORIZED",
            )

        try:
            PostService.delete_post(post_id=post_id, user_id=user_id)
            return PostPayload(success=True, post_id=post_id)
        except PostError as e:
            return PostPayload(
                success=False,
                message=e.message,
                error_code=e.code,
            )


@strawberry.type
class PostQueries:
    """Post domain GraphQL queries."""

    @strawberry.field(
        description="Retrieve a single post by its UUID with full engagement metrics and viewer context."
    )
    def post(
        self,
        info: strawberry.types.Info,
        id: strawberry.ID,
    ) -> FeedPost | None:
        """
        Fetch a single post entity by ID safely.

        Dynamically calculates viewer relationship (liked, saved, following) and
        returns the full content DTO including all media attachments.

        **Authentication:** Optional
        **Parameters:**
        - id (ID, required) - Valid Post UUID
        **Returns:** Nullable FeedPost
        **Errors:** Returns None gracefully if post is missing or deleted.
        """
        from core.feed.schema import _dto_to_feed_post
        from core.posts.services import PostService
        from core.shared.exceptions import PostError

        viewer_id = _get_authenticated_user_id(info)

        try:
            result = PostService.get_post(post_id=str(id), viewer_id=viewer_id)
            return _dto_to_feed_post(result)
        except PostError:
            return None

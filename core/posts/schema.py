"""GraphQL types, queries, and mutations for the posts domain."""


import strawberry

from core.users.schema import _get_authenticated_user_id


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
    """Response for post mutations."""

    success: bool
    post_id: str | None = None
    message: str | None = None
    error_code: str | None = None


@strawberry.type
class PostMutations:
    """Post domain GraphQL mutations."""

    @strawberry.mutation(description="Create a new post")
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
        """Create a new post with optional media and scripture reference."""
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

    @strawberry.mutation(description="Update a post's caption")
    def update_post(
        self,
        info: strawberry.types.Info,
        post_id: str,
        caption: str | None = None,
    ) -> PostPayload:
        """Update a post's caption within the edit window."""
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

    @strawberry.mutation(description="Delete a post (soft delete)")
    def delete_post(
        self,
        info: strawberry.types.Info,
        post_id: str,
    ) -> PostPayload:
        """Soft-delete a post."""
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

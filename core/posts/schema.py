from typing import Optional

import strawberry

from core.feed.schema import CategoryType
from core.media.schema import MediaFileType
from core.shared.types import ErrorType, MediaType, PostType
from core.users.schema import _get_authenticated_user_id


@strawberry.input
class ScriptureInput:
    book: str
    chapter: int
    verse_start: int
    verse_end: int | None = None
    version: str | None = "kjv"


@strawberry.type
class CreatePostPayload:
    """
    Standard response wrapper for Post mutations.
    """

    success: bool = strawberry.field(description="Whether the mutation succeeded")
    post: Optional["Post"] = strawberry.field(default=None, description="The resulting post")
    error: ErrorType | None = strawberry.field(default=None, description="Explicit error info")


@strawberry.type
class PostPayload:
    """Legacy response wrapper for update/delete post mutations."""

    success: bool
    post_id: str | None = None
    message: str | None = None
    error_code: str | None = None


@strawberry.type
class PostMutations:
    """Post domain GraphQL mutations."""

    @strawberry.mutation(
        description="Create a new multimedia app post. Supports Text, Media, and Bible variants."
    )
    def create_post(
        self,
        info: strawberry.types.Info,
        post_type: PostType,
        caption: str | None = None,
        category_id: str | None = None,
        media_ids: list[str] | None = None,
        media_type: MediaType | None = None,
        scripture_reference: ScriptureInput | None = None,
    ) -> CreatePostPayload:
        """
        Create a new post.

        **Steps:**
        1. Upload media first using uploadMedia mutation
        2. Get mediaId from upload response
        3. Call createPost with mediaIds

        **Validation:**
        - TEXT posts: Only caption or scripture required
        - MEDIA posts: mediaIds and mediaType required
        - BIBLE posts: scriptureReference required
        """
        from core.posts.services import PostService
        from core.shared.exceptions import PostError

        user_id = _get_authenticated_user_id(info)
        if not user_id:
            return CreatePostPayload(
                success=False,
                error=ErrorType(code="UNAUTHORIZED", message="Authentication required"),
            )

        # Validate post type specific requirements
        if post_type == PostType.TEXT and media_ids:
            return CreatePostPayload(
                success=False,
                error=ErrorType(
                    code="INVALID_POST_TYPE",
                    message="TEXT posts cannot have media",
                    field="mediaIds",
                ),
            )

        if post_type == PostType.MEDIA and not media_ids:
            return CreatePostPayload(
                success=False,
                error=ErrorType(
                    code="MISSING_REQUIRED_FIELD",
                    message="MEDIA posts require mediaIds",
                    field="mediaIds",
                ),
            )

        if post_type == PostType.BIBLE and not scripture_reference:
            return CreatePostPayload(
                success=False,
                error=ErrorType(
                    code="MISSING_REQUIRED_FIELD",
                    message="BIBLE posts require scripture_reference",
                    field="scripture_reference",
                ),
            )

        try:
            post_dto = PostService.create_post(
                user_id=user_id,
                post_type=post_type.value,
                caption=caption,
                category_id=category_id,
                media_ids=media_ids,
                media_type=media_type.value if media_type else None,
                scripture_reference=scripture_reference.__dict__ if scripture_reference else None,
            )

            # We'll map PostResponseDTO to the Post GraphQL type
            # Need to define the Post type first
            return CreatePostPayload(success=True, post=_dto_to_post(post_dto))
        except (PostError, ValueError) as e:
            code = getattr(e, "code", "VALIDATION_ERROR")
            message = getattr(e, "message", str(e))
            return CreatePostPayload(success=False, error=ErrorType(code=code, message=message))

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
class Post:
    id: str
    caption: str | None
    post_type: str
    created_at: str
    share_url: str
    category_id: str | None = None

    _dto: strawberry.Private[object] = None

    @strawberry.field
    def text(self) -> str | None:
        """Post caption/text - mobile expects 'text' field cleanly"""
        return self.caption

    @strawberry.field
    def media(self) -> list[MediaFileType]:
        """Return media files with full structure."""
        media_list = []
        if not self._dto or not self._dto.media:
            return media_list

        if self.post_type.lower() == "video":
            media_list.append(
                MediaFileType(
                    id=self.id,  # Using post ID for single video for now, or resolving from DTO
                    url=self._dto.media.url,
                    type=MediaType.VIDEO,
                    width=getattr(self._dto.media, "width", 0),
                    height=getattr(self._dto.media, "height", 0),
                    thumbnail=getattr(self._dto.media, "thumbnail_url", ""),
                )
            )
        elif self.post_type.lower() == "image":
            for img in self._dto.media.items:
                media_list.append(
                    MediaFileType(
                        id=img.id,
                        url=img.url,
                        type=MediaType.IMAGE,
                        width=img.width,
                        height=img.height,
                    )
                )
        return media_list

    @strawberry.field
    def category(self) -> CategoryType | None:
        """Full category object."""
        from core.feed.schema import FeedQueries

        if not self.category_id:
            return None

        # Resolve from static list or service
        categories = FeedQueries().discover_categories()
        for cat in categories:
            if cat.id == self.category_id:
                return CategoryType(id=cat.id, label=cat.label, slug=cat.slug, icon=cat.icon)
        return None


def _dto_to_post(dto) -> Post:
    """Map PostResponseDTO to GraphQL Post type."""
    return Post(
        id=dto.id,
        caption=dto.caption,
        post_type=dto.type,
        created_at=dto.created_at,
        share_url=dto.share_url,
        category_id=dto.category_id,
        _dto=dto,
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
    ) -> Post | None:
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
        from core.posts.services import PostService
        from core.shared.exceptions import PostError

        viewer_id = _get_authenticated_user_id(info)

        try:
            result = PostService.get_post(post_id=str(id), viewer_id=viewer_id)
            return _dto_to_post(result)
        except PostError:
            return None

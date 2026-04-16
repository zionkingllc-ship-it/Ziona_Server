import logging
from typing import Annotated, Optional

import strawberry

from core.feed.schema import CategoryType
from core.media.schema import MediaFileType
from core.scripture.constants import normalize_translation
from core.shared.types import ErrorType, MediaType, PostType, ScriptureVerse
from core.users.schema import _get_authenticated_user_id

logger = logging.getLogger("core.posts")


@strawberry.input
class ScriptureInput:
    book: str
    chapter: int
    verse_start: int
    verse_end: int | None = None
    translation: str | None = "KJV"


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
    error: ErrorType | None = strawberry.field(default=None)


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
        # -----------------------------------------------------------------
        # Content parameters — use the field that matches your post type:
        #   TEXT  posts → textMessage  (required)
        #   MEDIA posts → caption      (optional description under the media)
        #   BIBLE posts → bibleMessage (optional personal note on the verse)
        # Sending the wrong field for a post type will raise a validation error.
        # -----------------------------------------------------------------
        text_message: Annotated[
            str | None,
            strawberry.argument(
                name="textMessage",
                description="Content body for TEXT posts. Required when postType is TEXT.",
            ),
        ] = None,
        caption: Annotated[
            str | None,
            strawberry.argument(
                description="Optional description for MEDIA posts. Ignored for TEXT and BIBLE posts.",
            ),
        ] = None,
        bible_message: Annotated[
            str | None,
            strawberry.argument(
                name="bibleMessage",
                description="Optional personal note on a BIBLE post. Ignored for TEXT and MEDIA posts.",
            ),
        ] = None,
        category: str | None = None,
        media_ids: list[str] | None = None,
        media_urls: list[str] | None = None,
        media_type: MediaType | None = None,
        thumbnail_url: str | None = None,
        width: int | None = None,
        height: int | None = None,
        duration: int | None = None,
        scripture_book: str | None = None,
        scripture_chapter: int | None = None,
        scripture_verse_start: int | None = None,
        scripture_verse_end: int | None = None,
        scripture_translation: str | None = "KJV",
    ) -> CreatePostPayload:
        """
        Create a new post.

        **Content Field Contract:**
        - TEXT  posts: send `textMessage` (required — at least 1 char)
        - MEDIA posts: send `caption`     (optional description)
        - BIBLE posts: send `bibleMessage`(optional personal note)

        **Steps (MEDIA posts):**
        1. Upload media first using uploadMedia mutation
        2. Get mediaId from upload response
        3. Call createPost with mediaIds

        **Validation:**
        - TEXT  posts: textMessage required
        - MEDIA posts: mediaIds and mediaType required
        - BIBLE posts: scriptureReference required
        """
        from core.posts.services import PostService
        from core.shared.exceptions import PostError

        user_id = _get_authenticated_user_id(info)
        if not user_id:
            logger.warning("create_post_unauthorized")
            return CreatePostPayload(
                success=False,
                error=ErrorType(code="UNAUTHORIZED", message="Authentication required"),
            )

        # ── Route content to the correct internal field based on post type ──
        if post_type == PostType.TEXT:
            # textMessage is required for TEXT posts
            if not text_message or not text_message.strip():
                return CreatePostPayload(
                    success=False,
                    error=ErrorType(
                        code="TEXT_MESSAGE_REQUIRED",
                        message="textMessage is required for TEXT posts and cannot be empty.",
                        field="textMessage",
                    ),
                )
            if caption is not None:
                return CreatePostPayload(
                    success=False,
                    error=ErrorType(
                        code="CAPTION_FIELD_WRONG_TYPE",
                        message="Use textMessage (not caption) for TEXT posts.",
                        field="caption",
                    ),
                )
            resolved_caption = text_message

        elif post_type == PostType.MEDIA:
            # caption is optional for MEDIA posts; textMessage/bibleMessage are not applicable
            if text_message is not None:
                return CreatePostPayload(
                    success=False,
                    error=ErrorType(
                        code="CAPTION_FIELD_WRONG_TYPE",
                        message="Use caption (not textMessage) for MEDIA posts.",
                        field="textMessage",
                    ),
                )
            if bible_message is not None:
                return CreatePostPayload(
                    success=False,
                    error=ErrorType(
                        code="CAPTION_FIELD_WRONG_TYPE",
                        message="Use caption (not bibleMessage) for MEDIA posts.",
                        field="bibleMessage",
                    ),
                )
            resolved_caption = caption  # optional

        elif post_type == PostType.BIBLE:
            # bibleMessage is the optional personal note; caption/textMessage are not applicable
            if caption is not None:
                return CreatePostPayload(
                    success=False,
                    error=ErrorType(
                        code="CAPTION_FIELD_WRONG_TYPE",
                        message="Use bibleMessage (not caption) for BIBLE posts.",
                        field="caption",
                    ),
                )
            if text_message is not None:
                return CreatePostPayload(
                    success=False,
                    error=ErrorType(
                        code="CAPTION_FIELD_WRONG_TYPE",
                        message="Use bibleMessage (not textMessage) for BIBLE posts.",
                        field="textMessage",
                    ),
                )
            resolved_caption = bible_message  # optional
        else:
            resolved_caption = caption

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

        if post_type == PostType.MEDIA and not media_ids and not media_urls:
            return CreatePostPayload(
                success=False,
                error=ErrorType(
                    code="MEDIA_REQUIRED",
                    message="MEDIA posts require mediaIds or mediaUrls",
                    field="mediaUrls",
                ),
            )

        if post_type == PostType.BIBLE and not (
            scripture_book and scripture_chapter and scripture_verse_start
        ):
            return CreatePostPayload(
                success=False,
                error=ErrorType(
                    code="SCRIPTURE_FIELDS_REQUIRED",
                    message="BIBLE posts require explicit scripture fields",
                    field="scripture_book",
                ),
            )

        try:
            scripture_reference = None
            if scripture_book and scripture_chapter and scripture_verse_start:
                scripture_reference = {
                    "book": scripture_book,
                    "chapter": scripture_chapter,
                    "verse_start": scripture_verse_start,
                    "verse_end": scripture_verse_end,
                    "version": scripture_translation or "KJV",
                }

            post_dto = PostService.create_post(
                user_id=user_id,
                post_type=post_type.value,
                caption=resolved_caption,
                category_id=category,
                media_ids=media_ids,
                media_urls=media_urls,
                media_type=media_type.value if media_type else None,
                thumbnail_url=thumbnail_url,
                width=width,
                height=height,
                duration=duration,
                scripture_reference=scripture_reference,
            )

            # We'll map PostResponseDTO to the Post GraphQL type
            # Need to define the Post type first
            return CreatePostPayload(success=True, post=_dto_to_post(post_dto))
        except (PostError, ValueError) as e:
            code = getattr(e, "code", "VALIDATION_ERROR")
            message = getattr(e, "message", str(e))
            logger.warning(
                "create_post_failed user_id=%s code=%s message=%s", user_id, code, message
            )
            extensions = getattr(e, "extensions", {})
            field = extensions.get("field", None)

            details_dict = {k: v for k, v in extensions.items() if k != "field"}
            details = details_dict if details_dict else None

            return CreatePostPayload(
                success=False,
                error=ErrorType(code=code, message=message, field=field, details=details),
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
class PostAuthor:
    """Author info within a post."""

    id: str
    username: str
    avatar_url: str | None = None


@strawberry.type
class PostStats:
    """Engagement stats for a post."""

    likes_count: int = 0
    comments_count: int = 0
    shares_count: int = 0
    saves_count: int = 0


@strawberry.type
class PostViewerState:
    """Viewer's relationship to a post."""

    liked: bool = False
    saved: bool = False
    following_author: bool = False
    is_owner: bool = False


@strawberry.type
class PostScripture:
    """Scripture reference attached to a post."""

    reference: str
    text: str
    translation: str = strawberry.field(name="translation", default="KJV")
    book: str
    chapter: int
    verse_start: int
    verse_end: int | None = None
    verses: list[ScriptureVerse] = strawberry.field(default_factory=list)


@strawberry.type
class Post:
    id: str
    # Internal backing field - not exposed directly.
    # Use the computed caption / textMessage / text fields below.
    _caption: strawberry.Private[str | None] = None
    post_type: PostType = strawberry.field(name="type")
    created_at: str
    share_url: str

    _category_id: strawberry.Private[str | None] = None
    _dto: strawberry.Private[object] = None
    _raw_type: strawberry.Private[str | None] = None  # Original DTO type for media rendering

    @strawberry.field(description="Caption for MEDIA posts. Null for TEXT and BIBLE posts.")
    def caption(self) -> str | None:
        if self.post_type == PostType.MEDIA:
            return self._caption
        return None

    @strawberry.field(
        name="textMessage",
        description="Content body for TEXT posts only. Null for MEDIA and BIBLE posts.",
    )
    def text_message(self) -> str | None:
        if self.post_type == PostType.TEXT:
            return self._caption
        return None

    @strawberry.field(
        name="bibleMessage",
        description="Caption/note attached to a BIBLE post. Null for TEXT and MEDIA posts.",
    )
    def bible_message(self) -> str | None:
        if self.post_type == PostType.BIBLE:
            return self._caption
        return None

    @strawberry.field(description="Post author info")
    def author(self) -> PostAuthor | None:
        if not self._dto or not self._dto.author:
            return None
        return PostAuthor(
            id=self._dto.author.id,
            username=self._dto.author.username,
            avatar_url=self._dto.author.avatar_url,
        )

    @strawberry.field(description="Media files array")
    def media(self) -> list[MediaFileType]:
        media_list = []
        if not self._dto or not self._dto.media:
            return media_list

        # Use the raw DTO type (image/video) for media rendering, not the mapped PostType enum
        raw_type = (self._raw_type or "").lower()

        if raw_type == "video":
            media_list.append(
                MediaFileType(
                    id=self.id,
                    url=self._dto.media.url,
                    type=MediaType.VIDEO,
                    width=getattr(self._dto.media, "width", 0),
                    height=getattr(self._dto.media, "height", 0),
                    thumbnail_url=getattr(self._dto.media, "thumbnail_url", ""),
                )
            )
        elif raw_type == "image":
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

    @strawberry.field(description="Engagement statistics")
    def stats(self) -> PostStats:
        if not self._dto or not self._dto.stats:
            return PostStats()
        return PostStats(
            likes_count=self._dto.stats.likes_count,
            comments_count=self._dto.stats.comments_count,
            shares_count=self._dto.stats.shares_count,
            saves_count=self._dto.stats.saves_count,
        )

    @strawberry.field(description="Viewer's relationship to this post")
    def viewer_state(self) -> PostViewerState | None:
        if not self._dto or not self._dto.viewer_state:
            return None
        return PostViewerState(
            liked=self._dto.viewer_state.liked,
            saved=self._dto.viewer_state.saved,
            following_author=self._dto.viewer_state.following_author,
            is_owner=self._dto.viewer_state.is_owner,
        )

    @strawberry.field(description="Attached scripture reference")
    def scripture(self) -> PostScripture | None:
        if not self._dto or not self._dto.scripture:
            return None
        return PostScripture(
            reference=self._dto.scripture.reference,
            text=self._dto.scripture.text,
            translation=normalize_translation(self._dto.scripture.version),
            book=self._dto.scripture.book,
            chapter=self._dto.scripture.chapter,
            verse_start=self._dto.scripture.verse_start,
            verse_end=self._dto.scripture.verse_end,
            verses=[
                ScriptureVerse(number=v.number, text=v.text)
                for v in getattr(self._dto.scripture, "verses", [])
            ],
        )

    @strawberry.field(description="Full category object")
    def category(self) -> CategoryType | None:
        from core.feed.schema import _get_category_by_id

        if not self._category_id:
            return None

        return _get_category_by_id(self._category_id)


def _dto_to_post(dto) -> Post:
    """Map PostResponseDTO to GraphQL Post type."""
    post_t = dto.type.lower() if getattr(dto, "type", None) else "text"
    mapped_type = (
        PostType.MEDIA
        if post_t in ("image", "video")
        else (PostType.BIBLE if post_t == "bible" else PostType.TEXT)
    )

    if post_t not in ("image", "video", "text", "bible"):
        logger.warning("Unknown post type in DTO dto_type=%s post_id=%s", post_t, dto.id)

    return Post(
        id=dto.id,
        _caption=dto.caption,
        post_type=mapped_type,
        created_at=dto.created_at,
        share_url=dto.share_url,
        _category_id=dto.category_id,
        _dto=dto,
        _raw_type=dto.type,
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

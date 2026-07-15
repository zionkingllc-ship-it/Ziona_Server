"""Anchor + anchor-response GraphQL types.

Split from the former core/circles/schema.py (no contract change).
"""

import enum
from datetime import datetime

import strawberry
from strawberry.types import Info

from core.circles.schema._helpers import _anchor_date_value
from core.shared.types import ErrorType
from core.users.schema import UserType, _get_authenticated_user_id


@strawberry.enum
class AnchorTypeEnum(enum.Enum):
    BIBLE_VERSE = "bible_verse"
    DEVOTIONAL = "devotional"
    TEXT = "text"
    IMAGE = "image"
    VIDEO = "video"
    IMAGE_TEXT = "image_text"


@strawberry.type
class ScriptureReference:
    book: str
    chapter: int | None = None
    verse_start: int | None = strawberry.field(name="verseStart", default=None)
    verse_end: int | None = strawberry.field(name="verseEnd", default=None)
    translation: str = strawberry.field(name="translation", default="KJV")
    text: str = ""


@strawberry.type
class AnchorPageType:
    page_number: int = strawberry.field(name="pageNumber")
    title: str
    content: str
    media_url: str = strawberry.field(name="mediaUrl", default="")


@strawberry.type
class AnchorViewerState:
    """Viewer's engagement state on an Anchor."""

    liked: bool = False
    prayed: bool = False


@strawberry.type
class AnchorType:
    id: str
    title: str
    content: str

    @strawberry.field(name="anchorType")
    def anchor_type(self) -> str:
        return self._dto.anchor_type

    @strawberry.field(name="type")
    def mobile_type(self) -> str:
        return self._dto.anchor_type

    @strawberry.field(name="createdAt")
    def created_at(self) -> datetime:
        return self._dto.created_at

    @strawberry.field(name="publishedAt")
    def published_at(self) -> datetime:
        return self._dto.published_at

    @strawberry.field
    def date(self) -> str:
        return _anchor_date_value(self._dto)

    @strawberry.field(name="anchorDate")
    def anchor_date(self) -> str:
        return _anchor_date_value(self._dto)

    @strawberry.field(name="expiresAt")
    def expires_at(self) -> datetime:
        return self._dto.expires_at

    @strawberry.field(name="timeRemaining")
    def time_remaining(self) -> str:
        return self._dto.get_time_remaining()

    @strawberry.field(name="isActive")
    def is_active(self) -> bool:
        return self._dto.is_active

    @strawberry.field(name="isExpired")
    def is_expired(self) -> bool:
        """True once the anchor's 24-hour window has passed.

        Computed in Python from the already-loaded expires_at field — zero
        extra DB queries. The mobile app uses this flag to decide whether to
        render the anchor as active or show it in the expired history list.
        """
        from django.utils import timezone

        return timezone.now() >= self._dto.expires_at

    @strawberry.field(name="responseCount")
    def response_count(self) -> int:
        if hasattr(self._dto, "response_count"):
            return self._dto.response_count
        return self._dto.get_response_count()

    @strawberry.field(name="mediaUrl")
    def media_url(self) -> str | None:
        return self._dto.media_url or None

    @strawberry.field
    def scripture(self) -> str | None:
        return self.bible_reference()

    @strawberry.field(name="likedImage")
    def liked_image(self) -> int | None:
        # Kept for backward compatibility.
        # Mobile dev: migrate to viewerState.liked for per-user like state.
        return 1 if self._dto.anchor_liked_count else None

    @strawberry.field(name="viewerState")
    def viewer_state(self, info: Info) -> AnchorViewerState | None:
        """Per-user like/pray state for this anchor.

        Dynamically resolved (not cached) because the Anchor object itself
        is served from Redis — the viewer state must always be fresh.
        """
        from core.circles.models import AnchorEngagement

        viewer_id = _get_authenticated_user_id(info)
        if not viewer_id:
            return AnchorViewerState(liked=False, prayed=False)

        engagements = set(
            AnchorEngagement.objects.filter(
                anchor_id=self.id,
                user_id=viewer_id,
            ).values_list("engagement_type", flat=True)
        )
        return AnchorViewerState(
            liked="like" in engagements,
            prayed="pray" in engagements,
        )

    @strawberry.field(name="bibleReference")
    def bible_reference(self) -> str | None:
        if not self._dto.scripture_book:
            return None

        reference = self._dto.scripture_book
        if self._dto.scripture_chapter:
            reference = f"{reference} {self._dto.scripture_chapter}"
        if self._dto.scripture_verse_start:
            reference = f"{reference}:{self._dto.scripture_verse_start}"
            if (
                self._dto.scripture_verse_end
                and self._dto.scripture_verse_end != self._dto.scripture_verse_start
            ):
                reference = f"{reference}-{self._dto.scripture_verse_end}"
        return reference

    @strawberry.field(name="bibleText")
    def bible_text(self) -> str | None:
        return self._dto.scripture_text or None

    @strawberry.field(name="scriptureReference")
    def scripture_reference(self) -> ScriptureReference | None:
        if self._dto.anchor_type != "bible_verse" or not self._dto.scripture_book:
            return None
        return ScriptureReference(
            book=self._dto.scripture_book,
            chapter=self._dto.scripture_chapter,
            verse_start=self._dto.scripture_verse_start,
            verse_end=self._dto.scripture_verse_end,
            translation=self._dto.scripture_translation,
            text=self._dto.scripture_text,
        )

    @strawberry.field
    def pages(self) -> list[AnchorPageType]:
        if self._dto.anchor_type != "devotional":
            return []
        return [
            AnchorPageType(
                page_number=p.page_number,
                title=p.title,
                content=p.content,
                media_url=p.media_url or "",
            )
            for p in self._dto.pages.all()
        ]

    @strawberry.field
    def author(self) -> UserType | None:
        if self._dto.created_by:
            return UserType.from_db_model(self._dto.created_by)
        return None

    # ── Mobile engagement counters ──────────────────────────────────────────

    @strawberry.field(name="prayedCount")
    def prayed_count(self) -> int:
        return self._dto.prayed_count or 0

    @strawberry.field(name="anchorLikedCount")
    def anchor_liked_count(self) -> int:
        return self._dto.anchor_liked_count or 0

    # ── Visual / theming fields ──────────────────────────────────────────────

    @strawberry.field(name="backgroundColors")
    def background_colors(self) -> list[str] | None:
        val = self._dto.background_colors
        return val if val else None

    @strawberry.field(name="backgroundImage")
    def background_image(self) -> str | None:
        return self._dto.background_image or None

    @strawberry.field(name="anchorText")
    def anchor_text(self) -> str | None:
        return self._dto.anchor_text or None

    @strawberry.field(name="anchorVerse")
    def anchor_verse(self) -> str | None:
        return self._dto.anchor_verse or None

    @strawberry.field(name="anchorImageText")
    def anchor_image_text(self) -> str | None:
        return self._dto.anchor_image_text or None

    @strawberry.field(name="anchorThumbnail")
    def anchor_thumbnail(self) -> str | None:
        return self._dto.anchor_thumbnail or self._dto.preview_url or None

    @strawberry.field(name="anchorImage")
    def anchor_image(self) -> str | None:
        if self._dto.anchor_image:
            return self._dto.anchor_image
        if self._dto.anchor_type in ("image", "image_text"):
            return self._dto.media_url or None
        return None

    @strawberry.field(name="anchorVideo")
    def anchor_video(self) -> str | None:
        if self._dto.anchor_video:
            return self._dto.anchor_video
        if self._dto.anchor_type == "video":
            return self._dto.media_url or None
        return None

    @classmethod
    def from_db_model(cls, anchor_instance):
        if not anchor_instance:
            return None
        instance = cls(
            id=str(anchor_instance.id),
            title=anchor_instance.title,
            content=anchor_instance.content or "",
        )
        instance._dto = anchor_instance
        return instance


@strawberry.type
class CreateAnchorPayload:
    success: bool
    anchor: AnchorType | None = None
    error: ErrorType | None = None


@strawberry.enum
class ResponseTypeEnum(enum.Enum):
    REFLECTION = "reflection"
    PRAYER = "prayer"
    QUESTION = "question"
    REPLY = "reply"


@strawberry.enum
class ReactionTypeEnum(enum.Enum):
    AMEN = "amen"
    ENCOURAGED = "encouraged"
    THOUGHTFUL = "thoughtful"


@strawberry.enum
class ResponseSortEnum(enum.Enum):
    RECENT = "RECENT"
    TRENDING = "TRENDING"


@strawberry.type
class AnchorResponseReactionType:
    id: str
    user: UserType
    reaction_type: str = strawberry.field(name="reactionType")
    created_at: datetime = strawberry.field(name="createdAt")


@strawberry.type
class AnchorResponseType:
    id: str
    content: str

    @strawberry.field(name="responseType")
    def response_type(self) -> str:
        return self._dto.response_type

    @strawberry.field(name="mediaUrl")
    def media_url(self) -> str | None:
        return self._dto.media_url or None

    @strawberry.field(name="mediaType")
    def media_type(self) -> str | None:
        return self._dto.media_type or None

    @strawberry.field(name="reactionCount")
    def reaction_count(self) -> int:
        return self._dto.reaction_count

    @strawberry.field(name="createdAt")
    def created_at(self) -> datetime:
        return self._dto.created_at

    @strawberry.field
    def author(self) -> UserType:
        return UserType.from_db_model(self._dto.user)

    @strawberry.field(name="replyCount")
    def reply_count(self) -> int:
        return self._dto.replies.filter(deleted_at__isnull=True).count()

    @strawberry.field(name="viewerReactionType")
    def viewer_reaction_type(self) -> str | None:
        # Populated by annotate in `get_anchor_responses`
        if hasattr(self._dto, "viewer_reaction_type"):
            return self._dto.viewer_reaction_type
        return None

    @classmethod
    def from_db_model(cls, instance):
        if not instance:
            return None
        obj = cls(
            id=str(instance.id),
            content=instance.content or "",
        )
        obj._dto = instance
        return obj


@strawberry.type
class AnchorResponsePayload:
    success: bool
    response: AnchorResponseType | None = None
    error: ErrorType | None = None


@strawberry.type
class ReactionPayload:
    success: bool
    reaction: AnchorResponseReactionType | None = None
    error: ErrorType | None = None


@strawberry.type
class CircleReportPayload:
    success: bool
    error: ErrorType | None = None

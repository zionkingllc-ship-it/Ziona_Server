# ──────────────────────────────────────────────
#  Enums
# ──────────────────────────────────────────────
import enum
from datetime import datetime

import strawberry
from strawberry.types import Info

from core.circles.services import (
    get_all_circles,
    get_my_circles,
    get_suggested_circles,
    join_circle,
    leave_circle,
)
from core.shared.exceptions import ZionaError
from core.shared.types import ErrorType
from core.users.schema import UserType, _get_authenticated_user_id


@strawberry.enum
class AnchorTypeEnum(enum.Enum):
    BIBLE_VERSE = "bible_verse"
    DEVOTIONAL = "devotional"
    IMAGE = "image"
    VIDEO = "video"


# ──────────────────────────────────────────────
#  Strawberry Types
# ──────────────────────────────────────────────


@strawberry.type
class ScriptureReference:
    book: str
    chapter: int | None = None
    verse_start: int | None = strawberry.field(name="verseStart", default=None)
    verse_end: int | None = strawberry.field(name="verseEnd", default=None)
    version: str = "KJV"
    text: str = ""


@strawberry.type
class AnchorPageType:
    page_number: int = strawberry.field(name="pageNumber")
    title: str
    content: str
    media_url: str = strawberry.field(name="mediaUrl", default="")


@strawberry.type
class AnchorType:
    id: str
    title: str
    content: str

    @strawberry.field(name="anchorType")
    def anchor_type(self) -> str:
        return self._dto.anchor_type

    @strawberry.field(name="createdAt")
    def created_at(self) -> datetime:
        return self._dto.created_at

    @strawberry.field(name="publishedAt")
    def published_at(self) -> datetime:
        return self._dto.published_at

    @strawberry.field(name="expiresAt")
    def expires_at(self) -> datetime:
        return self._dto.expires_at

    @strawberry.field(name="timeRemaining")
    def time_remaining(self) -> str:
        return self._dto.get_time_remaining()

    @strawberry.field(name="isActive")
    def is_active(self) -> bool:
        return self._dto.is_active

    @strawberry.field(name="responseCount")
    def response_count(self) -> int:
        if hasattr(self._dto, "response_count"):
            return self._dto.response_count
        return self._dto.get_response_count()

    @strawberry.field(name="mediaUrl")
    def media_url(self) -> str | None:
        return self._dto.media_url or None

    @strawberry.field(name="scriptureReference")
    def scripture_reference(self) -> ScriptureReference | None:
        if self._dto.anchor_type != "bible_verse" or not self._dto.scripture_book:
            return None
        return ScriptureReference(
            book=self._dto.scripture_book,
            chapter=self._dto.scripture_chapter,
            verse_start=self._dto.scripture_verse_start,
            verse_end=self._dto.scripture_verse_end,
            version=self._dto.scripture_version,
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


@strawberry.type
class CircleRule:
    rule_number: int = strawberry.field(name="ruleNumber")
    title: str
    description: str


# ──────────────────────────────────────────────
#  Circle Type
# ──────────────────────────────────────────────


@strawberry.type
class CircleType:
    id: str
    name: str
    description: str

    @strawberry.field(name="coverImage")
    def cover_image(self) -> str:
        return self._dto.cover_image

    @strawberry.field(name="createdAt")
    def created_at(self) -> datetime:
        return self._dto.created_at

    @strawberry.field(name="memberCount")
    def member_count(self) -> int:
        if hasattr(self._dto, "member_count"):
            return self._dto.member_count
        return self._dto.get_member_count()

    @strawberry.field(name="memberPreviews")
    def member_previews(self) -> list[UserType]:
        if hasattr(self._dto, "preview_memberships"):
            return [UserType.from_db_model(m.user) for m in self._dto.preview_memberships]
        return [UserType.from_db_model(u) for u in self._dto.get_member_previews(limit=4)]

    @strawberry.field(name="isSubscribed")
    def is_subscribed(self, info: Info) -> bool:
        if hasattr(self._dto, "_is_viewer_subscribed"):
            return self._dto._is_viewer_subscribed

        user_id = _get_authenticated_user_id(info)
        return self._dto.is_user_subscribed(user_id)

    @strawberry.field
    def rules(self) -> list[CircleRule]:
        from core.circles.models import CircleRule as CircleRuleModel

        return [
            CircleRule(rule_number=rule.rule_number, title=rule.title, description=rule.description)
            for rule in CircleRuleModel.get_default_rules()
        ]

    @strawberry.field(name="activeAnchor")
    def active_anchor(self) -> AnchorType | None:
        from core.circles.anchor_services import get_active_anchor

        anchor = get_active_anchor(str(self._dto.id))
        return AnchorType.from_db_model(anchor)

    @classmethod
    def from_db_model(cls, circle_instance):
        if not circle_instance:
            return None
        instance = cls(
            id=str(circle_instance.id),
            name=circle_instance.name,
            description=circle_instance.description,
        )
        instance._dto = circle_instance
        return instance


@strawberry.type
class JoinCirclePayload:
    success: bool
    circle: CircleType | None = None
    error: ErrorType | None = None


# ──────────────────────────────────────────────
#  Queries
# ──────────────────────────────────────────────


@strawberry.type
class CircleQueries:
    @strawberry.field(name="allCircles")
    def all_circles(
        self, info: Info, limit: int = 20, cursor: str | None = None
    ) -> list[CircleType]:
        viewer_id = _get_authenticated_user_id(info)
        circles = get_all_circles(viewer_id, limit, cursor)
        return [CircleType.from_db_model(c) for c in circles]

    @strawberry.field(name="myCircles")
    def my_circles(self, info: Info, limit: int = 20) -> list[CircleType]:
        viewer_id = _get_authenticated_user_id(info)
        if not viewer_id:
            return []
        circles = get_my_circles(viewer_id, limit)
        return [CircleType.from_db_model(c) for c in circles]

    @strawberry.field(name="suggestedCircles")
    def suggested_circles(self, info: Info, limit: int = 10) -> list[CircleType]:
        viewer_id = _get_authenticated_user_id(info)
        circles = get_suggested_circles(viewer_id, limit)
        return [CircleType.from_db_model(c) for c in circles]

    @strawberry.field
    def circle(self, id: str) -> CircleType | None:
        from core.circles.models import Circle

        try:
            circle = Circle.objects.get(id=id, is_active=True, deleted_at__isnull=True)
            return CircleType.from_db_model(circle)
        except Circle.DoesNotExist:
            return None

    @strawberry.field(name="activeAnchor")
    def active_anchor(self, circle_id: str) -> AnchorType | None:
        from core.circles.anchor_services import get_active_anchor

        anchor = get_active_anchor(circle_id)
        return AnchorType.from_db_model(anchor)

    @strawberry.field(name="anchorHistory")
    def anchor_history(
        self, circle_id: str, limit: int = 20, cursor: str | None = None
    ) -> list[AnchorType]:
        from core.circles.anchor_services import get_anchor_history

        anchors = get_anchor_history(circle_id, limit, cursor)
        return [AnchorType.from_db_model(a) for a in anchors]

    @strawberry.field(name="anchorByDate")
    def anchor_by_date(self, circle_id: str, date: str) -> AnchorType | None:
        from datetime import date as date_type

        from core.circles.anchor_services import get_anchor_by_date

        try:
            parsed_date = date_type.fromisoformat(date)
        except ValueError:
            return None
        anchor = get_anchor_by_date(circle_id, parsed_date)
        return AnchorType.from_db_model(anchor)

    @strawberry.field(name="anchorResponses")
    def anchor_responses(
        self,
        info: Info,
        anchor_id: str,
        sort: str = "TRENDING",
        my_posts_only: bool = False,
        limit: int = 50,
        cursor: str | None = None,
    ) -> list["AnchorResponseType"]:
        viewer_id = _get_authenticated_user_id(info)
        from core.circles.response_services import get_anchor_responses

        responses = get_anchor_responses(
            anchor_id=anchor_id,
            viewer_id=viewer_id,
            sort=sort,
            my_posts_only=my_posts_only,
            limit=limit,
            cursor=cursor,
        )
        return [AnchorResponseType.from_db_model(r) for r in responses]

    @strawberry.field(name="responseReplies")
    def response_replies(
        self, info: Info, response_id: str, limit: int = 50
    ) -> list["AnchorResponseType"]:
        viewer_id = _get_authenticated_user_id(info)
        from core.circles.response_services import get_response_replies

        replies = get_response_replies(response_id=response_id, viewer_id=viewer_id, limit=limit)
        return [AnchorResponseType.from_db_model(r) for r in replies]


# ──────────────────────────────────────────────
#  Mutations
# ──────────────────────────────────────────────

# ──────────────────────────────────────────────
#  Phase 3 & 4 Enums
# ──────────────────────────────────────────────


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


# ──────────────────────────────────────────────
#  Phase 3 & 4 Types
# ──────────────────────────────────────────────


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


@strawberry.type
class CircleMutations:
    @strawberry.mutation(name="joinCircle")
    def join_circle(self, info: Info, circle_id: str) -> JoinCirclePayload:
        viewer_id = _get_authenticated_user_id(info)
        if not viewer_id:
            return JoinCirclePayload(
                success=False,
                error=ErrorType(
                    code="UNAUTHORIZED", message="You must be logged in to join a Circle"
                ),
            )

        try:
            membership = join_circle(viewer_id, circle_id)
            circle = membership.circle
            circle._is_viewer_subscribed = True
            return JoinCirclePayload(success=True, circle=CircleType.from_db_model(circle))
        except ZionaError as e:
            return JoinCirclePayload(success=False, error=ErrorType(code=e.code, message=e.message))

    @strawberry.mutation(name="leaveCircle")
    def leave_circle(self, info: Info, circle_id: str) -> JoinCirclePayload:
        viewer_id = _get_authenticated_user_id(info)
        if not viewer_id:
            return JoinCirclePayload(
                success=False,
                error=ErrorType(
                    code="UNAUTHORIZED", message="You must be logged in to leave a Circle"
                ),
            )

        try:
            leave_circle(viewer_id, circle_id)
            return JoinCirclePayload(success=True)
        except ZionaError as e:
            return JoinCirclePayload(success=False, error=ErrorType(code=e.code, message=e.message))

    @strawberry.mutation(name="createAnchor")
    def create_anchor(
        self,
        info: Info,
        circle_id: str,
        anchor_type: str,
        title: str,
        content: str = "",
        published_at: str | None = None,
        scripture_book: str = "",
        scripture_chapter: int | None = None,
        scripture_verse_start: int | None = None,
        scripture_verse_end: int | None = None,
        scripture_version: str = "KJV",
        scripture_text: str = "",
        media_url: str = "",
    ) -> CreateAnchorPayload:
        viewer_id = _get_authenticated_user_id(info)
        if not viewer_id:
            return CreateAnchorPayload(
                success=False,
                error=ErrorType(
                    code="UNAUTHORIZED", message="You must be logged in to create an anchor"
                ),
            )

        try:
            from datetime import datetime as dt

            from core.circles.anchor_services import create_anchor as _create_anchor

            parsed_published_at = None
            if published_at:
                parsed_published_at = dt.fromisoformat(published_at)

            anchor = _create_anchor(
                creator_id=viewer_id,
                circle_id=circle_id,
                anchor_type=anchor_type,
                title=title,
                content=content,
                published_at=parsed_published_at,
                scripture_book=scripture_book,
                scripture_chapter=scripture_chapter,
                scripture_verse_start=scripture_verse_start,
                scripture_verse_end=scripture_verse_end,
                scripture_version=scripture_version,
                scripture_text=scripture_text,
                media_url=media_url,
            )
            return CreateAnchorPayload(success=True, anchor=AnchorType.from_db_model(anchor))
        except ZionaError as e:
            return CreateAnchorPayload(
                success=False, error=ErrorType(code=e.code, message=e.message)
            )

    @strawberry.mutation(name="respondToAnchor")
    def respond_to_anchor(
        self,
        info: Info,
        anchor_id: str,
        response_type: str,
        content: str,
        media_url: str = "",
        media_type: str = "",
    ) -> AnchorResponsePayload:
        viewer_id = _get_authenticated_user_id(info)
        if not viewer_id:
            return AnchorResponsePayload(
                success=False, error=ErrorType(code="UNAUTHORIZED", message="Login required")
            )
        try:
            from core.circles.response_services import create_response

            response = create_response(
                user_id=viewer_id,
                anchor_id=anchor_id,
                response_type=response_type,
                content=content,
                media_url=media_url,
                media_type=media_type,
            )
            return AnchorResponsePayload(
                success=True, response=AnchorResponseType.from_db_model(response)
            )
        except ZionaError as e:
            return AnchorResponsePayload(
                success=False, error=ErrorType(code=e.code, message=e.message)
            )

    @strawberry.mutation(name="replyToResponse")
    def reply_to_response(
        self,
        info: Info,
        parent_response_id: str,
        content: str,
        media_url: str = "",
        media_type: str = "",
    ) -> AnchorResponsePayload:
        viewer_id = _get_authenticated_user_id(info)
        if not viewer_id:
            return AnchorResponsePayload(
                success=False, error=ErrorType(code="UNAUTHORIZED", message="Login required")
            )
        try:
            from core.circles.response_services import create_reply

            reply = create_reply(
                user_id=viewer_id,
                parent_response_id=parent_response_id,
                content=content,
                media_url=media_url,
                media_type=media_type,
            )
            return AnchorResponsePayload(
                success=True, response=AnchorResponseType.from_db_model(reply)
            )
        except ZionaError as e:
            return AnchorResponsePayload(
                success=False, error=ErrorType(code=e.code, message=e.message)
            )

    @strawberry.mutation(name="reactToResponse")
    def react_to_response(
        self, info: Info, response_id: str, reaction_type: str
    ) -> ReactionPayload:
        viewer_id = _get_authenticated_user_id(info)
        if not viewer_id:
            return ReactionPayload(
                success=False, error=ErrorType(code="UNAUTHORIZED", message="Login required")
            )
        try:
            from core.circles.response_services import toggle_reaction

            reaction = toggle_reaction(
                user_id=viewer_id, response_id=response_id, reaction_type=reaction_type
            )
            if reaction:
                reaction_obj = AnchorResponseReactionType(
                    id=str(reaction.id),
                    user=UserType.from_db_model(reaction.user),
                    reaction_type=reaction.reaction_type,
                    created_at=reaction.created_at,
                )
                return ReactionPayload(success=True, reaction=reaction_obj)
            return ReactionPayload(success=True, reaction=None)  # Toggled off
        except ZionaError as e:
            return ReactionPayload(success=False, error=ErrorType(code=e.code, message=e.message))

    @strawberry.mutation(name="reportCircleContent")
    def report_circle_content(
        self, info: Info, circle_id: str, target_type: str, target_id: str, reason: str
    ) -> CircleReportPayload:
        viewer_id = _get_authenticated_user_id(info)
        if not viewer_id:
            return CircleReportPayload(
                success=False, error=ErrorType(code="UNAUTHORIZED", message="Login required")
            )
        try:
            from core.circles.moderation_services import report_circle_content as do_report

            do_report(
                reporter_id=viewer_id,
                circle_id=circle_id,
                target_type=target_type,
                target_id=target_id,
                reason=reason,
            )
            return CircleReportPayload(success=True)
        except ZionaError as e:
            return CircleReportPayload(
                success=False, error=ErrorType(code=e.code, message=e.message)
            )

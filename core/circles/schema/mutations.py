"""Circle GraphQL mutations.

Split from the former core/circles/schema.py (no contract change).
"""


import strawberry
from strawberry.types import Info

from core.circles.schema.anchors import (
    AnchorResponsePayload,
    AnchorResponseReactionType,
    AnchorResponseType,
    AnchorType,
    CircleReportPayload,
    CreateAnchorPayload,
    ReactionPayload,
)
from core.circles.schema.circles import CircleType, JoinCirclePayload
from core.circles.schema.posts import (
    AnchorEngagementPayload,
    CirclePostCommentLikePayload,
    CirclePostCommentPayload,
    CirclePostCommentType,
    CirclePostEngagementPayload,
    CirclePostType,
    CreateCirclePostPayload,
    LikeCirclePostPayload,
)
from core.circles.services import (
    create_circle_post,
    ensure_circle_post_liked,
    join_circle,
    leave_circle,
    like_anchor,
    like_circle_post,
    pray_for_anchor,
    pray_for_circle_post,
)
from core.shared.exceptions import ZionaError
from core.shared.types import ErrorType
from core.shared.types import MediaType as GraphQLMediaType
from core.users.schema import UserType, _get_authenticated_user_id


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
        scripture_translation: str = "KJV",
        scripture_text: str = "",
        media_url: str = "",
        anchor_text: str = "",
        anchor_verse: str = "",
        background_colors: list[str] | None = None,
        background_image: str = "",
        anchor_image: str = "",
        anchor_video: str = "",
        anchor_image_text: str = "",
        anchor_thumbnail: str = "",
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
                scripture_translation=scripture_translation,
                scripture_text=scripture_text,
                media_url=media_url,
                anchor_text=anchor_text,
                anchor_verse=anchor_verse,
                background_colors=background_colors,
                background_image=background_image,
                anchor_image=anchor_image,
                anchor_video=anchor_video,
                anchor_image_text=anchor_image_text,
                anchor_thumbnail=anchor_thumbnail,
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

    @strawberry.mutation(name="createCirclePost")
    def create_circle_post(
        self,
        info: Info,
        circle_id: str,
        text: str | None = None,
        media_ids: list[str] | None = None,
        media_urls: list[str] | None = None,
        media_type: GraphQLMediaType | None = None,
        thumbnail_url: str | None = None,
        width: int | None = None,
        height: int | None = None,
        duration: int | None = None,
    ) -> CreateCirclePostPayload:
        viewer_id = _get_authenticated_user_id(info)
        if not viewer_id:
            return CreateCirclePostPayload(
                success=False,
                error=ErrorType(code="UNAUTHORIZED", message="Login required"),
            )
        try:
            post = create_circle_post(
                user_id=viewer_id,
                circle_id=circle_id,
                text=text or "",
                media_ids=media_ids,
                media_urls=media_urls,
                media_type=media_type.value if media_type else None,
                thumbnail_url=thumbnail_url,
                width=width,
                height=height,
                duration=duration,
            )
            return CreateCirclePostPayload(success=True, post=CirclePostType.from_db_model(post))
        except ZionaError as e:
            return CreateCirclePostPayload(
                success=False, error=ErrorType(code=e.code, message=e.message)
            )

    @strawberry.mutation(name="prayForAnchor")
    def pray_for_anchor(self, info: Info, anchor_id: str) -> AnchorEngagementPayload:
        viewer_id = _get_authenticated_user_id(info)
        if not viewer_id:
            return AnchorEngagementPayload(
                success=False,
                error=ErrorType(code="UNAUTHORIZED", message="Login required"),
            )
        try:
            result = pray_for_anchor(user_id=viewer_id, anchor_id=anchor_id)
            return AnchorEngagementPayload(
                success=True,
                prayed=result["prayed"],
                prayed_count=result["prayed_count"],
            )
        except ZionaError as e:
            return AnchorEngagementPayload(
                success=False, error=ErrorType(code=e.code, message=e.message)
            )

    @strawberry.mutation(name="likeAnchor")
    def like_anchor(self, info: Info, anchor_id: str) -> AnchorEngagementPayload:
        viewer_id = _get_authenticated_user_id(info)
        if not viewer_id:
            return AnchorEngagementPayload(
                success=False,
                error=ErrorType(code="UNAUTHORIZED", message="Login required"),
            )
        try:
            result = like_anchor(user_id=viewer_id, anchor_id=anchor_id)
            return AnchorEngagementPayload(
                success=True,
                liked=result["liked"],
                anchor_liked_count=result["anchor_liked_count"],
            )
        except ZionaError as e:
            return AnchorEngagementPayload(
                success=False, error=ErrorType(code=e.code, message=e.message)
            )

    @strawberry.mutation(name="prayForCirclePost")
    def pray_for_circle_post(self, info: Info, post_id: str) -> CirclePostEngagementPayload:
        viewer_id = _get_authenticated_user_id(info)
        if not viewer_id:
            return CirclePostEngagementPayload(
                success=False,
                error=ErrorType(code="UNAUTHORIZED", message="Login required"),
            )
        try:
            result = pray_for_circle_post(user_id=viewer_id, post_id=post_id)
            return CirclePostEngagementPayload(
                success=True,
                prayed=result["prayed"],
                prayed_count=result["prayed_count"],
            )
        except ZionaError as e:
            return CirclePostEngagementPayload(
                success=False, error=ErrorType(code=e.code, message=e.message)
            )

    @strawberry.mutation(
        name="likeCirclePost",
        description="Toggle a like on a CirclePost. Returns the new like state and count.",
    )
    def like_circle_post(self, info: Info, post_id: str) -> LikeCirclePostPayload:
        viewer_id = _get_authenticated_user_id(info)
        if not viewer_id:
            return LikeCirclePostPayload(
                success=False,
                error=ErrorType(code="UNAUTHORIZED", message="Login required"),
            )
        try:
            result = like_circle_post(user_id=viewer_id, post_id=post_id)
            return LikeCirclePostPayload(
                success=True,
                liked=result["liked"],
                likes_count=result["likes_count"],
            )
        except ZionaError as e:
            return LikeCirclePostPayload(
                success=False, error=ErrorType(code=e.code, message=e.message)
            )

    @strawberry.mutation(
        name="ensureCirclePostLiked",
        description="Idempotently like a CirclePost. Repeated calls keep it liked.",
    )
    def ensure_circle_post_liked(self, info: Info, post_id: str) -> LikeCirclePostPayload:
        viewer_id = _get_authenticated_user_id(info)
        if not viewer_id:
            return LikeCirclePostPayload(
                success=False,
                error=ErrorType(code="UNAUTHORIZED", message="Login required"),
            )
        try:
            result = ensure_circle_post_liked(user_id=viewer_id, post_id=post_id)
            return LikeCirclePostPayload(
                success=True,
                liked=result["liked"],
                likes_count=result["likes_count"],
            )
        except ZionaError as e:
            return LikeCirclePostPayload(
                success=False, error=ErrorType(code=e.code, message=e.message)
            )

    # ── Phase 6: Circle Post Comment mutations ─────────────────────────────

    @strawberry.mutation(
        name="commentOnCirclePost",
        description="Add an inline comment to a CirclePost.",
    )
    def comment_on_circle_post(
        self, info: Info, post_id: str, text: str
    ) -> "CirclePostCommentPayload":
        viewer_id = _get_authenticated_user_id(info)
        if not viewer_id:
            return CirclePostCommentPayload(
                success=False, error=ErrorType(code="UNAUTHORIZED", message="Login required")
            )
        try:
            from core.circles.comment_services import create_circle_post_comment

            comment = create_circle_post_comment(user_id=viewer_id, post_id=post_id, text=text)
            return CirclePostCommentPayload(
                success=True, comment=CirclePostCommentType.from_db_model(comment)
            )
        except ZionaError as e:
            return CirclePostCommentPayload(
                success=False, error=ErrorType(code=e.code, message=e.message)
            )

    @strawberry.mutation(
        name="deleteCirclePostComment",
        description="Soft-delete your own comment on a CirclePost.",
    )
    def delete_circle_post_comment(self, info: Info, comment_id: str) -> "CirclePostCommentPayload":
        viewer_id = _get_authenticated_user_id(info)
        if not viewer_id:
            return CirclePostCommentPayload(
                success=False, error=ErrorType(code="UNAUTHORIZED", message="Login required")
            )
        try:
            from core.circles.comment_services import delete_circle_post_comment

            delete_circle_post_comment(user_id=viewer_id, comment_id=comment_id)
            return CirclePostCommentPayload(success=True)
        except ZionaError as e:
            return CirclePostCommentPayload(
                success=False, error=ErrorType(code=e.code, message=e.message)
            )

    @strawberry.mutation(
        name="likeCirclePostComment",
        description="Toggle a like on a CirclePost comment. Returns new like state and count.",
    )
    def like_circle_post_comment(
        self, info: Info, comment_id: str
    ) -> "CirclePostCommentLikePayload":
        viewer_id = _get_authenticated_user_id(info)
        if not viewer_id:
            return CirclePostCommentLikePayload(
                success=False, error=ErrorType(code="UNAUTHORIZED", message="Login required")
            )
        try:
            from core.circles.comment_services import toggle_circle_post_comment_like

            liked, likes_count = toggle_circle_post_comment_like(
                user_id=viewer_id, comment_id=comment_id
            )
            return CirclePostCommentLikePayload(success=True, liked=liked, likes_count=likes_count)
        except ZionaError as e:
            return CirclePostCommentLikePayload(
                success=False, error=ErrorType(code=e.code, message=e.message)
            )

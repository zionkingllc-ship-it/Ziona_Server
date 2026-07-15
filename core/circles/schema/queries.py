"""Circle GraphQL queries.

Split from the former core/circles/schema.py (no contract change).
"""


import strawberry
from strawberry.types import Info

from core.circles.schema._helpers import _unique_anchor_dates
from core.circles.schema.anchors import (
    AnchorResponseType,
    AnchorType,
)
from core.circles.schema.circles import CircleType, _circle_rules_for
from core.circles.schema.posts import (
    CircleFeedDataType,
    CircleFeedResponse,
    CirclePostCommentsResponse,
    CirclePostCommentType,
    CirclePostFilterEnum,
    CirclePostType,
    _build_circle_feed_response,
    _resolve_circle_feed_filters,
)
from core.circles.services import (
    get_all_circles,
    get_circle_by_id,
    get_circle_feed,
    get_circle_post,
    get_my_circles,
    get_suggested_circles,
)
from core.shared.exceptions import ZionaError
from core.shared.types import PageInfo
from core.users.schema import _get_authenticated_user_id


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
    def circle(self, info: Info, id: str) -> CircleType | None:
        viewer_id = _get_authenticated_user_id(info)
        circle = get_circle_by_id(id, viewer_id=viewer_id)
        return CircleType.from_db_model(circle)

    @strawberry.field(name="activeAnchor")
    def active_anchor(self, info: Info, circle_id: str) -> AnchorType | None:
        from core.circles.anchor_services import get_active_anchor

        viewer_id = _get_authenticated_user_id(info)
        anchor = get_active_anchor(circle_id, viewer_id=viewer_id)
        return AnchorType.from_db_model(anchor)

    @strawberry.field(name="anchorHistory")
    def anchor_history(
        self,
        info: Info,
        circle_id: str,
        limit: int = 20,
        cursor: str | None = None,
        include_active: bool = True,
    ) -> list[AnchorType]:
        from core.circles.anchor_services import get_anchor_history

        viewer_id = _get_authenticated_user_id(info)
        anchors = get_anchor_history(
            circle_id,
            limit,
            cursor,
            include_active=include_active,
            viewer_id=viewer_id,
        )
        return [AnchorType.from_db_model(a) for a in anchors]

    @strawberry.field(name="circleFeed")
    def circle_feed(
        self,
        info: Info,
        circle_id: str,
        page: int = 1,
        page_size: int = 20,
        sort_by: str = "NEW",
        author_id: str | None = None,
        circle_filter: CirclePostFilterEnum | None = None,
    ) -> CircleFeedResponse:
        viewer_id = _get_authenticated_user_id(info)
        return _build_circle_feed_response(
            circle_id,
            page,
            page_size,
            viewer_id,
            sort_by=sort_by,
            author_id=author_id,
            circle_filter=circle_filter,
        )

    @strawberry.field(name="circlePosts")
    def circle_posts(
        self,
        info: Info,
        circle_id: str,
        page: int = 1,
        page_size: int = 20,
        sort_by: str = "NEW",
        author_id: str | None = None,
        circle_filter: CirclePostFilterEnum | None = None,
    ) -> CircleFeedResponse:
        viewer_id = _get_authenticated_user_id(info)
        return _build_circle_feed_response(
            circle_id,
            page,
            page_size,
            viewer_id,
            sort_by=sort_by,
            author_id=author_id,
            circle_filter=circle_filter,
        )

    @strawberry.field(
        name="circlePost",
        description="Fetch a single CirclePost by ID. Use this for post detail screens.",
    )
    def circle_post(self, info: Info, id: str) -> CirclePostType | None:
        viewer_id = _get_authenticated_user_id(info)
        try:
            post = get_circle_post(post_id=id, viewer_id=viewer_id)
            return CirclePostType.from_db_model(post)
        except ZionaError:
            return None

    @strawberry.field(
        name="circlePostComments",
        description="Paginated inline comments for a CirclePost, with viewer like state.",
    )
    def circle_post_comments(
        self,
        info: Info,
        post_id: str,
        page: int = 1,
        page_size: int = 30,
    ) -> "CirclePostCommentsResponse":
        from core.circles.comment_services import get_circle_post_comments

        viewer_id = _get_authenticated_user_id(info)
        comments, has_next_page, total_count = get_circle_post_comments(
            post_id=post_id,
            viewer_id=viewer_id,
            page=page,
            page_size=page_size,
        )
        return CirclePostCommentsResponse(
            comments=[CirclePostCommentType.from_db_model(c) for c in comments],
            page_info=PageInfo(
                has_next_page=has_next_page,
                total_count=total_count,
                current_page=page,
            ),
        )

    @strawberry.field(
        name="anchor",
        description="Fetch a single Anchor by ID. Use this for deep-link/push-notification screens.",
    )
    def anchor_by_id(self, info: Info, id: str) -> AnchorType | None:
        from core.circles.anchor_services import get_anchor_by_id

        try:
            anchor = get_anchor_by_id(anchor_id=id, viewer_id=_get_authenticated_user_id(info))
            return AnchorType.from_db_model(anchor)
        except ZionaError:
            return None

    @strawberry.field(name="circleFeedData")
    def circle_feed_data(
        self,
        info: Info,
        circle_id: str,
        page: int = 1,
        page_size: int = 20,
        history_limit: int = 5,
        sort_by: str = "NEW",
        author_id: str | None = None,
        circle_filter: CirclePostFilterEnum | None = None,
    ) -> CircleFeedDataType | None:
        from core.circles.anchor_services import get_active_anchor, get_anchor_history

        viewer_id = _get_authenticated_user_id(info)
        circle = get_circle_by_id(circle_id, viewer_id=viewer_id)
        if not circle:
            return None
        sort_by, author_id = _resolve_circle_feed_filters(
            viewer_id=viewer_id,
            sort_by=sort_by,
            author_id=author_id,
            circle_filter=circle_filter,
        )
        if circle_filter == CirclePostFilterEnum.VIEWER_POSTS and not viewer_id:
            posts = []
        else:
            posts, _, _ = get_circle_feed(
                circle_id,
                page,
                page_size,
                viewer_id=viewer_id,
                sort_by=sort_by,
                author_id=author_id,
            )
        # Only return anchors within the 5-day window so the mobile app
        # naturally stops showing older ones once the purge task removes them.
        past_anchors = get_anchor_history(
            circle_id,
            limit=history_limit,
            include_active=False,
            max_age_days=5,
            viewer_id=viewer_id,
        )
        active_anchor = get_active_anchor(circle_id, viewer_id=viewer_id)
        circle_type = CircleType.from_db_model(circle)
        return CircleFeedDataType(
            banner_image=circle_type.banner_image(),
            profile_image=circle.profile_image_url or None,
            cover_image=circle.cover_image or None,
            suggestion_card_image=circle.cover_image or None,
            name=circle.name,
            description=circle.description,
            member_count=circle.display_member_count
            if circle.display_member_count is not None
            else circle.get_member_count(),
            is_joined=circle.is_user_subscribed(viewer_id),
            active_anchor=AnchorType.from_db_model(active_anchor),
            anchor_dates=_unique_anchor_dates(active_anchor, past_anchors),
            past_anchors=[AnchorType.from_db_model(anchor) for anchor in past_anchors],
            posts=[CirclePostType.from_db_model(post) for post in posts],
            member_avatars=circle_type.member_avatars(),
            rules=_circle_rules_for(circle),
        )

    @strawberry.field(name="anchorByDate")
    def anchor_by_date(self, info: Info, circle_id: str, date: str) -> AnchorType | None:
        from datetime import date as date_type

        from core.circles.anchor_services import get_anchor_by_date

        try:
            parsed_date = date_type.fromisoformat(date)
        except ValueError:
            return None
        anchor = get_anchor_by_date(
            circle_id,
            parsed_date,
            viewer_id=_get_authenticated_user_id(info),
        )
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

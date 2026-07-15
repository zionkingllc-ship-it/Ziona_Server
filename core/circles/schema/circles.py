"""Circle GraphQL types (circle, rules, membership payloads).

Split from the former core/circles/schema.py (no contract change).
"""

from datetime import datetime

import strawberry
from strawberry.types import Info

from core.circles.schema._helpers import _unique_anchor_dates
from core.circles.schema.anchors import AnchorType
from core.shared.types import ErrorType
from core.users.schema import UserType, _get_authenticated_user_id


@strawberry.type
class CircleRule:
    id: int
    rule_number: int = strawberry.field(name="ruleNumber")
    title: str
    description: str


@strawberry.type
class CircleType:
    id: str
    name: str
    description: str

    def _banner_image_url(self) -> str | None:
        return (
            self._dto.banner_image or self._dto.cover_image or self._dto.profile_image_url or None
        )

    @strawberry.field
    def title(self) -> str:
        return self._dto.name

    @strawberry.field(name="coverImage")
    def cover_image(self) -> str:
        return self._dto.cover_image

    @strawberry.field(name="suggestionCardImage")
    def suggestion_card_image(self) -> str:
        return self._dto.cover_image

    @strawberry.field
    def image(self) -> str:
        return self._dto.cover_image

    @strawberry.field(name="createdAt")
    def created_at(self) -> datetime:
        return self._dto.created_at

    @strawberry.field(name="memberCount")
    def member_count(self) -> int:
        if self._dto.display_member_count is not None:
            return self._dto.display_member_count
        if hasattr(self._dto, "member_count"):
            return self._dto.member_count
        return self._dto.get_member_count()

    @strawberry.field
    def members(self) -> int:
        return self.member_count()

    @strawberry.field(name="memberPreviews")
    def member_previews(self) -> list[UserType]:
        if hasattr(self._dto, "preview_memberships"):
            return [UserType.from_db_model(m.user) for m in self._dto.preview_memberships]
        return [UserType.from_db_model(u) for u in self._dto.get_member_previews(limit=4)]

    @strawberry.field
    def avatars(self) -> list[str]:
        if hasattr(self._dto, "preview_memberships"):
            users = [m.user for m in self._dto.preview_memberships]
        else:
            users = self._dto.get_member_previews(limit=4)
        return [user.avatar_url for user in users if getattr(user, "avatar_url", "")]

    @strawberry.field(name="memberAvatars")
    def member_avatars(self) -> list[str]:
        users = self._dto.get_member_previews(limit=8)
        return [user.avatar_url for user in users if getattr(user, "avatar_url", "")]

    @strawberry.field(name="isSubscribed")
    def is_subscribed(self, info: Info) -> bool:
        if hasattr(self._dto, "_is_viewer_subscribed"):
            return self._dto._is_viewer_subscribed

        user_id = _get_authenticated_user_id(info)
        return self._dto.is_user_subscribed(user_id)

    @strawberry.field(name="isJoined")
    def is_joined(self, info: Info) -> bool:
        return self.is_subscribed(info)

    @strawberry.field
    def rules(self) -> list[CircleRule]:
        return _circle_rules_for(self._dto)

    @strawberry.field(name="activeAnchor")
    def active_anchor(self, info: Info) -> AnchorType | None:
        from core.circles.anchor_services import get_active_anchor

        viewer_id = _get_authenticated_user_id(info)
        anchor = get_active_anchor(str(self._dto.id), viewer_id=viewer_id)
        return AnchorType.from_db_model(anchor)

    @strawberry.field(name="anchorDates")
    def anchor_dates(self, info: Info) -> list[str]:
        from core.circles.models import Anchor
        from core.engagement.cache import EngagementCache
        from core.engagement.hidden_content import exclude_hidden_circle_content

        viewer_id = _get_authenticated_user_id(info)
        if viewer_id and EngagementCache.is_circle_content_hidden(
            viewer_id, "circle", str(self._dto.id)
        ):
            return []

        anchors = Anchor.objects.filter(
            circle_id=self._dto.id,
            deleted_at__isnull=True,
        ).order_by("-published_at")
        anchors = exclude_hidden_circle_content(anchors, viewer_id, target_type="anchor")
        return _unique_anchor_dates(list(anchors))

    @strawberry.field(name="bannerImage")
    def banner_image(self) -> str | None:
        return self._banner_image_url()

    @strawberry.field(name="profileImage")
    def profile_image(self) -> str | None:
        return self._dto.profile_image_url or None

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


def _circle_rules_for(circle) -> list[CircleRule]:
    from core.circles.models import CircleRule as CircleRuleModel

    scoped_rules = list(circle.rules.all())
    rules = scoped_rules or list(CircleRuleModel.get_default_rules())
    return [
        CircleRule(
            id=rule.rule_number,
            rule_number=rule.rule_number,
            title=rule.title,
            description=rule.description,
        )
        for rule in rules
    ]

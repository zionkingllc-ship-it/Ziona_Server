"""
Phase 3: Service Layer for Anchor Responses and Reactions.
Handles respond_to_anchor, reply_to_response, sorting algorithms (Trending),
and faith-based reactions.
"""

from django.db import transaction
from django.db.models import F, OuterRef, Subquery
from django.utils import timezone

from core.circles.models import Anchor, AnchorResponse, AnchorResponseReaction, CircleMembership
from core.circles.validators import validate_response_media
from core.shared.exceptions import ZionaError

# ──────────────────────────────────────────────
#  Reactions
# ──────────────────────────────────────────────


@transaction.atomic
def toggle_reaction(user_id: str, response_id: str, reaction_type: str) -> AnchorResponseReaction:
    """
    Toggle a faith-based reaction on an AnchorResponse.
    If the user already reacted, updates the reaction type or removes it if same type.
    """
    valid_types = ["amen", "encouraged", "thoughtful"]
    if reaction_type not in valid_types:
        raise ZionaError(message="Invalid reaction type", code="INVALID_REACTION_TYPE")

    try:
        response = AnchorResponse.objects.select_for_update().get(
            id=response_id, deleted_at__isnull=True
        )
    except AnchorResponse.DoesNotExist:
        raise ZionaError(message="Response not found", code="RESPONSE_NOT_FOUND") from None

    reaction = AnchorResponseReaction.objects.filter(response=response, user_id=user_id).first()

    if reaction:
        if reaction.reaction_type == reaction_type:
            # Toggle off
            reaction.delete()
            # Decrement denormalized count
            AnchorResponse.objects.filter(id=response_id).update(
                reaction_count=F("reaction_count") - 1
            )
            reaction = None
        else:
            # Change type
            reaction.reaction_type = reaction_type
            reaction.save(update_fields=["reaction_type"])
    else:
        # Toggle on
        reaction = AnchorResponseReaction.objects.create(
            response=response, user_id=user_id, reaction_type=reaction_type
        )
        # Increment denormalized count
        AnchorResponse.objects.filter(id=response_id).update(reaction_count=F("reaction_count") + 1)

    return reaction


# ──────────────────────────────────────────────
#  Responses & Replies
# ──────────────────────────────────────────────


@transaction.atomic
def create_response(
    user_id: str,
    anchor_id: str,
    response_type: str,
    content: str,
    media_url: str = "",
    media_type: str = "",
) -> AnchorResponse:
    """Create a top-level response to an anchor."""
    try:
        anchor = Anchor.objects.get(id=anchor_id, deleted_at__isnull=True)
    except Anchor.DoesNotExist:
        raise ZionaError(message="Anchor not found", code="ANCHOR_NOT_FOUND") from None

    # Member check
    if not CircleMembership.objects.filter(circle=anchor.circle, user_id=user_id).exists():
        raise ZionaError(message="You must join the Circle to respond", code="NOT_CIRCLE_MEMBER")

    if anchor.is_expired:
        raise ZionaError(message="Cannot respond to an expired anchor", code="ANCHOR_EXPIRED")

    # Validation
    valid_types = ["reflection", "prayer", "question"]
    if response_type not in valid_types:
        raise ZionaError(message="Invalid response type", code="INVALID_RESPONSE_TYPE")

    # Media validation (15-30s video check)
    validate_response_media(media_type, media_url)

    return AnchorResponse.objects.create(
        user_id=user_id,
        anchor=anchor,
        response_type=response_type,
        content=content,
        media_url=media_url,
        media_type=media_type,
    )


@transaction.atomic
def create_reply(
    user_id: str, parent_response_id: str, content: str, media_url: str = "", media_type: str = ""
) -> AnchorResponse:
    """Create a reply to an existing response. Enforces maximum threading depth of 2."""
    try:
        parent = AnchorResponse.objects.get(id=parent_response_id, deleted_at__isnull=True)
    except AnchorResponse.DoesNotExist:
        raise ZionaError(message="Parent response not found", code="RESPONSE_NOT_FOUND") from None

    # Member check
    if not CircleMembership.objects.filter(circle=parent.anchor.circle, user_id=user_id).exists():
        raise ZionaError(message="You must join the Circle to reply", code="NOT_CIRCLE_MEMBER")

    # Enforce Threading Depth (prevent 3rd level replies)
    if parent.parent_response_id is not None:
        raise ZionaError(
            message="Maximum threading depth exceeded", code="THREADING_DEPTH_EXCEEDED"
        )

    # Media validation
    validate_response_media(media_type, media_url)

    return AnchorResponse.objects.create(
        user_id=user_id,
        anchor=parent.anchor,
        parent_response=parent,
        response_type="reply",
        content=content,
        media_url=media_url,
        media_type=media_type,
    )


# ──────────────────────────────────────────────
#  Retrieval & Trending Algorithm
# ──────────────────────────────────────────────


def get_anchor_responses(
    anchor_id: str,
    viewer_id: str,
    sort: str = "TRENDING",
    my_posts_only: bool = False,
    limit: int = 50,
    cursor: str | None = None,
) -> list[AnchorResponse]:
    """
    Get top-level responses for an anchor.
    Sort algorithm:
      - RECENT: order by -created_at
      - TRENDING: annotation based formula -> (reaction_count * 2) - hours_since
    Returns N+1 optimized queryset.
    """
    queryset = AnchorResponse.objects.filter(
        anchor_id=anchor_id,
        parent_response__isnull=True,
        deleted_at__isnull=True,
    ).select_related("user")

    if my_posts_only and viewer_id:
        queryset = queryset.filter(user_id=viewer_id)

    if sort == "TRENDING":
        # Default RECENT order initially
        queryset = queryset.order_by("-created_at")
    else:
        # Default RECENT sort
        queryset = queryset.order_by("-created_at")

    # Add boolean for 'has_viewer_reacted' using subquery
    viewer_reaction = AnchorResponseReaction.objects.filter(
        response=OuterRef("pk"), user_id=viewer_id
    ).values("reaction_type")[:1]

    queryset = queryset.annotate(viewer_reaction_type=Subquery(viewer_reaction))

    results = list(queryset[:limit])

    if sort == "TRENDING":
        now = timezone.now()

        def get_trending_score(response):
            # Same formula: (reaction_count * 2) - hours_since
            hours = (now - response.created_at).total_seconds() / 3600
            return (response.reaction_count * 2) - hours

        results.sort(key=get_trending_score, reverse=True)

    if cursor:
        pass  # Not fully implementing pagination cursor logic for brevity right now

    return results


def get_response_replies(response_id: str, viewer_id: str, limit: int = 50) -> list[AnchorResponse]:
    """Get replies to a specific response, ordered oldest-to-newest."""
    queryset = (
        AnchorResponse.objects.filter(parent_response_id=response_id, deleted_at__isnull=True)
        .select_related("user")
        .order_by("created_at")
    )

    viewer_reaction = AnchorResponseReaction.objects.filter(
        response=OuterRef("pk"), user_id=viewer_id
    ).values("reaction_type")[:1]

    queryset = queryset.annotate(viewer_reaction_type=Subquery(viewer_reaction))

    return list(queryset[:limit])

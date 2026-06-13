"""
Phase 4: Notifications for Faith Circles.
Handles sending push notifications for:
- New Anchors published in a joined circle
- Replies to a user's response
- Batched reactions to a user's responses
"""

# In a real Ziona app, this would import from core.notifications.services
# For now, we stub push notification sending logic to log or queue.
import logging

from core.circles.models import Anchor, AnchorResponse, AnchorResponseReaction, CircleMembership

logger = logging.getLogger(__name__)


def send_new_anchor_notification(anchor_id: str):
    """
    Called when a new anchor becomes active.
    Notifies all subscribed members of the circle.
    """
    try:
        anchor = Anchor.objects.get(id=anchor_id)
    except Anchor.DoesNotExist:
        return

    memberships = CircleMembership.objects.filter(
        circle=anchor.circle, is_subscribed=True
    ).select_related("user")

    # In production, dispatch async push notification task to FCM/APNS here
    for membership in memberships:
        logger.info(
            f"Notification -> {membership.user.email}: New Devotional in {anchor.circle.name}"
        )


def send_response_notification(reply_id: str):
    """
    Called when a user replies to someone else's response.
    Notifies the parent response author.
    """
    try:
        reply = AnchorResponse.objects.select_related(
            "parent_response__user", "user", "anchor__circle"
        ).get(id=reply_id)
    except AnchorResponse.DoesNotExist:
        return

    if not reply.parent_response:
        return  # Not a reply

    parent_author = reply.parent_response.user
    reply_author = reply.user

    if parent_author.id == reply_author.id:
        return  # Don't notify self

    # In production, dispatch async push notification task
    logger.info(
        f"Notification -> {parent_author.email}: {reply_author.email} replied to your reflection in {reply.anchor.circle.name}"
    )


def process_batched_reaction_notifications():
    """
    Scheduled via Celery Beat (e.g., hourly).
    Aggregates new reactions and sends batched notifications (e.g. "John and 4 others reacted Amen").
    """
    from datetime import timedelta

    from django.utils import timezone

    now = timezone.now()
    one_hour_ago = now - timedelta(hours=1)

    # Find all responses that received new reactions in the last hour
    recent_reactions = AnchorResponseReaction.objects.filter(
        created_at__gte=one_hour_ago
    ).select_related("response__user", "user")

    # Group by response author
    notifications_to_send = {}

    for reaction in recent_reactions:
        author_id = str(reaction.response.user.id)
        if author_id == str(reaction.user.id):
            continue  # Skip self-reactions

        if author_id not in notifications_to_send:
            notifications_to_send[author_id] = {
                "user": reaction.response.user,
                "response": reaction.response,
                "reactors": set(),
                "types": set(),
            }

        notifications_to_send[author_id]["reactors"].add(reaction.user.email)
        notifications_to_send[author_id]["types"].add(reaction.reaction_type)

    # Send batched pushes
    for struct in notifications_to_send.values():
        count = len(struct["reactors"])
        reactors_list = list(struct["reactors"])

        msg = f"{reactors_list[0]} "
        if count > 1:
            msg += f"and {count - 1} others "

        msg += "reacted to your reflection."
        logger.info(f"Batched Notification -> {struct['user'].email}: {msg}")

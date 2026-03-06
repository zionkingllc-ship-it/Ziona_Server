"""
Notification placeholder tasks.

These are stub tasks that will be implemented in Milestone 3.
For now they log the notification intent and return.
"""

import logging

from celery import shared_task

logger = logging.getLogger("core.notifications")


@shared_task(name="notifications.send_comment_mention_notification")
def send_comment_mention_notification(
    mentioned_user_ids: list[str],
    commenter_id: str,
    comment_id: str,
    post_id: str,
) -> None:
    """Send a notification to users mentioned in a comment.

    Placeholder — will be implemented in Milestone 3 (Push Notifications).

    Args:
        mentioned_user_ids: List of mentioned user UUIDs.
        commenter_id: UUID of the commenter.
        comment_id: UUID of the comment.
        post_id: UUID of the post.
    """
    logger.info(
        "notification_placeholder:comment_mention",
        extra={
            "mentioned_user_ids": mentioned_user_ids,
            "commenter_id": commenter_id,
            "comment_id": comment_id,
            "post_id": post_id,
        },
    )


@shared_task(name="notifications.send_follow_notification")
def send_follow_notification(
    follower_id: str,
    following_id: str,
) -> None:
    """Send a notification when someone follows a user.

    Placeholder — will be implemented in Milestone 3.

    Args:
        follower_id: UUID of the new follower.
        following_id: UUID of the followed user.
    """
    logger.info(
        "notification_placeholder:follow",
        extra={
            "follower_id": follower_id,
            "following_id": following_id,
        },
    )


@shared_task(name="notifications.send_share_notification")
def send_share_notification(
    sharer_id: str,
    recipient_id: str,
    post_id: str,
    share_id: str,
) -> None:
    """Send a notification when a post is shared with a user.

    Placeholder — will be implemented in Milestone 3.

    Args:
        sharer_id: UUID of the sharing user.
        recipient_id: UUID of the recipient.
        post_id: UUID of the shared post.
        share_id: UUID of the share record.
    """
    logger.info(
        "notification_placeholder:share",
        extra={
            "sharer_id": sharer_id,
            "recipient_id": recipient_id,
            "post_id": post_id,
            "share_id": share_id,
        },
    )


@shared_task(name="notifications.send_like_notification")
def send_like_notification(
    liker_id: str,
    post_id: str,
    post_author_id: str,
) -> None:
    """Send a notification when a post is liked.

    Placeholder — will be implemented in Milestone 3.

    Args:
        liker_id: UUID of the user who liked.
        post_id: UUID of the liked post.
        post_author_id: UUID of the post author.
    """
    logger.info(
        "notification_placeholder:like",
        extra={
            "liker_id": liker_id,
            "post_id": post_id,
            "post_author_id": post_author_id,
        },
    )

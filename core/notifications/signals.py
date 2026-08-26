import logging

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from core.circles.models import Anchor
from core.engagement.models import Comment, CommentLike, Like
from core.notifications.models import Notification, NotificationStatus, NotificationType
from core.notifications.services import (
    batch_like_notifications,
    create_notification,
    notify_mentions,
)
from core.posts.models import Post

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Comment)
def handle_comment_notifications(sender, instance, created, **kwargs):
    """Trigger notifications for comment replies and mentions."""
    if not created:
        return

    try:
        # Mentions — routed through the shared pipeline (deduplication,
        # preference checks, and self-mention guards are all handled there).
        mention_notifications = notify_mentions(
            text=instance.text or "",
            actor=instance.user,
            reference_id=instance.id,
            reference_type="comment",
        )
        mentioned_user_ids = {str(n.user_id) for n in mention_notifications}

        # Notify the post/comment author with a dedicated "New Reply" title.
        # Skip if they were already @-mentioned in the same comment — the mention
        # notification is more specific, so we don't double-notify.
        if instance.parent_comment_id:
            parent = Comment.objects.get(id=instance.parent_comment_id)
            if parent.user_id != instance.user_id and str(parent.user_id) not in mentioned_user_ids:
                create_notification(
                    user_id=parent.user_id,
                    type_str=NotificationType.REPLY_COMMENT,
                    reference_id=instance.id,
                    reference_type="comment",
                    title="New Reply",
                    message=f"{instance.user.username} replied to your comment",
                    sender_id=instance.user_id,
                )
        else:
            post = instance.post
            if post.user_id != instance.user_id and str(post.user_id) not in mentioned_user_ids:
                create_notification(
                    user_id=post.user_id,
                    type_str=NotificationType.REPLY_POST,
                    reference_id=instance.id,
                    reference_type="comment",
                    title="New Reply",
                    message=f"{instance.user.username} replied to your post",
                    sender_id=instance.user_id,
                )
    except Exception as e:
        logger.error(f"Error handling comment notification: {e}", exc_info=True)


@receiver(post_save, sender=Like)
def handle_like_notifications(sender, instance, created, **kwargs):
    """Trigger batched notifications for likes on posts."""
    if not created:
        return

    try:
        actor_username = instance.user.username
        if instance.post_id:
            post = instance.post
            if post.user_id != instance.user_id:
                batch_like_notifications(
                    actor_username=actor_username,
                    recipient_id=post.user_id,
                    reference_id=post.id,
                    reference_type="post",
                    like_type=NotificationType.LIKE_POST,
                    actor_id=instance.user_id,
                )
    except Exception as e:
        logger.error(f"Error handling like notification: {e}", exc_info=True)


@receiver(post_save, sender=CommentLike)
def handle_comment_like_notifications(sender, instance, created, **kwargs):
    """Trigger batched notifications for likes on comments and replies."""
    if not created:
        return

    try:
        comment = instance.comment
        if comment.user_id == instance.user_id:
            return

        batch_like_notifications(
            actor_username=instance.user.username,
            recipient_id=comment.user_id,
            reference_id=comment.id,
            reference_type="comment",
            like_type=NotificationType.LIKE_COMMENT,
            actor_id=instance.user_id,
        )
    except Exception as e:
        logger.error(f"Error handling comment like notification: {e}", exc_info=True)


@receiver(post_save, sender=Anchor)
def handle_anchor_notifications(sender, instance, created, **kwargs):
    """
    Handle anchor notifications.
    Anchors use daily batch processing so we DO NOT trigger immediate notifications here.
    """
    if not created:
        return
    # Marked for daily batch processing by Celery task (send_daily_anchor_notifications)
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Issue #8: Orphaned notification cleanup on content deletion
#
# Notification.reference_id is a plain UUIDField (not a FK), so Django cannot
# automatically cascade when a Post or Comment is deleted. Without these
# signals, tapping a notification for deleted content causes a 404 on the
# client. We soft-delete matching notifications so the row is preserved for
# analytics but is invisible to the user.
# ─────────────────────────────────────────────────────────────────────────────


@receiver(post_delete, sender=Post)
def cleanup_notifications_on_post_delete(sender, instance, **kwargs):
    """Soft-delete any notifications whose reference_id points to this post."""
    try:
        updated = Notification.objects.filter(
            reference_id=instance.id,
            reference_type="post",
            status=NotificationStatus.ACTIVE,
        ).update(status=NotificationStatus.DELETED)
        if updated:
            logger.info(
                "orphaned_notifications_cleaned",
                extra={"post_id": str(instance.id), "count": updated},
            )
    except Exception as e:
        logger.error(
            f"Error cleaning notifications for deleted post {instance.id}: {e}", exc_info=True
        )


@receiver(post_delete, sender=Comment)
def cleanup_notifications_on_comment_delete(sender, instance, **kwargs):
    """Soft-delete any notifications whose reference_id points to this comment."""
    try:
        updated = Notification.objects.filter(
            reference_id=instance.id,
            reference_type="comment",
            status=NotificationStatus.ACTIVE,
        ).update(status=NotificationStatus.DELETED)
        if updated:
            logger.info(
                "orphaned_notifications_cleaned",
                extra={"comment_id": str(instance.id), "count": updated},
            )
    except Exception as e:
        logger.error(
            f"Error cleaning notifications for deleted comment {instance.id}: {e}", exc_info=True
        )

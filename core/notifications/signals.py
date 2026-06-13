import logging
import re

from django.contrib.auth import get_user_model
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from core.circles.models import Anchor
from core.engagement.models import Comment, Like
from core.notifications.models import Notification, NotificationStatus, NotificationType
from core.notifications.services import batch_like_notifications, create_notification
from core.posts.models import Post

logger = logging.getLogger(__name__)
User = get_user_model()

MENTION_REGEX = re.compile(r"@(\w+)")


@receiver(post_save, sender=Comment)
def handle_comment_notifications(sender, instance, created, **kwargs):
    """Trigger notifications for comment replies and mentions."""
    if not created:
        return

    try:
        # Extract and notify mentions
        mentions = MENTION_REGEX.findall(instance.text or "")
        if mentions:
            mentioned_users = User.objects.filter(username__in=mentions)
            for m_user in mentioned_users:
                if m_user.id != instance.user_id:
                    create_notification(
                        user_id=m_user.id,
                        type_str=NotificationType.MENTION,
                        reference_id=instance.id,
                        reference_type="comment",
                        message=f"{instance.user.username} mentioned you in a comment",
                    )

        # Notify post/comment author
        if instance.parent_comment_id:
            parent = Comment.objects.get(id=instance.parent_comment_id)
            if parent.user_id != instance.user_id:
                create_notification(
                    user_id=parent.user_id,
                    type_str=NotificationType.REPLY_COMMENT,
                    reference_id=instance.id,
                    reference_type="comment",
                    message=f"{instance.user.username} replied to your comment",
                )
        else:
            post = instance.post
            if post.user_id != instance.user_id:
                create_notification(
                    user_id=post.user_id,
                    type_str=NotificationType.REPLY_POST,
                    reference_id=instance.id,
                    reference_type="comment",
                    message=f"{instance.user.username} replied to your post",
                )
    except Exception as e:
        logger.error(f"Error handling comment notification: {e}", exc_info=True)


@receiver(post_save, sender=Like)
def handle_like_notifications(sender, instance, created, **kwargs):
    """Trigger batched notifications for likes on posts and comments."""
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
                )
        elif instance.comment_id:
            comment = instance.comment
            if comment.user_id != instance.user_id:
                batch_like_notifications(
                    actor_username=actor_username,
                    recipient_id=comment.user_id,
                    reference_id=comment.id,
                    reference_type="comment",
                    like_type=NotificationType.LIKE_COMMENT,
                )
    except Exception as e:
        logger.error(f"Error handling like notification: {e}", exc_info=True)


@receiver(post_save, sender=Post)
def handle_post_notifications(sender, instance, created, **kwargs):
    """Trigger notifications for new circle posts."""
    if not created:
        return

    try:
        if hasattr(instance, "circle") and instance.circle_id:
            circle = instance.circle
            if hasattr(circle, "memberships"):
                members = circle.memberships.select_related("user")
                for membership in members:
                    member_id = membership.user_id
                    if member_id != instance.user_id:
                        create_notification(
                            user_id=member_id,
                            type_str=NotificationType.NEW_CIRCLE_POST,
                            reference_id=instance.id,
                            reference_type="post",
                            message=f"New post in {circle.name}",
                        )
    except Exception as e:
        logger.error(f"Error handling post notification: {e}", exc_info=True)


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

"""Shared account lifecycle helpers.

These helpers are intentionally used by user-facing account actions rather
than hard-deleting rows directly. They revoke sessions, remove side records,
hide visible user content, and anonymize identity fields in one place.
"""

from __future__ import annotations

from datetime import datetime

from django.db.models import Q

from core.users.models import UserRole, UserStatus


def revoke_user_sessions(user_id, *, delete_device_tokens: bool) -> None:
    """Revoke refresh tokens and deactivate/delete push device tokens."""
    from core.authentication.tokens import TokenService
    from core.notifications.models import DeviceToken

    TokenService.revoke_all_user_tokens(str(user_id))
    tokens = DeviceToken.objects.filter(user_id=user_id)
    if delete_device_tokens:
        tokens.delete()
    else:
        tokens.update(is_active=False)


def remove_or_hide_user_data(user, now: datetime) -> None:
    """Remove user-owned side records and hide visible user-generated content."""
    from core.circles.models import (
        Anchor,
        AnchorEngagement,
        AnchorResponse,
        AnchorResponseReaction,
        Circle,
        CircleMembership,
        CirclePost,
        CirclePostComment,
        CirclePostCommentLike,
        CirclePostEngagement,
        CircleReport,
        HiddenCircleContent,
    )
    from core.engagement.models import (
        BookmarkFolder,
        Comment,
        CommentLike,
        HiddenComment,
        HiddenPost,
        Like,
        Save,
        Share,
    )
    from core.follows.models import Follow
    from core.media.models import MediaFile
    from core.moderation.models import Report
    from core.notifications.models import Notification, NotificationPreference
    from core.posts.models import Post
    from core.users.models import UserInterest

    CircleMembership.objects.filter(user=user).delete()
    Follow.objects.filter(Q(follower=user) | Q(following=user)).delete()
    UserInterest.objects.filter(user=user).delete()

    Like.objects.filter(Q(user=user) | Q(post__user=user)).delete()
    CommentLike.objects.filter(Q(user=user) | Q(comment__user=user)).delete()
    Save.objects.filter(Q(user=user) | Q(post__user=user)).delete()
    BookmarkFolder.objects.filter(user=user).delete()
    Share.objects.filter(Q(user=user) | Q(post__user=user)).delete()
    Share.objects.filter(recipient=user).update(recipient=None)
    HiddenComment.objects.filter(Q(user=user) | Q(comment__user=user)).delete()
    HiddenPost.objects.filter(Q(user=user) | Q(post__user=user)).delete()
    HiddenCircleContent.objects.filter(user=user).delete()

    AnchorEngagement.objects.filter(Q(user=user) | Q(anchor__created_by=user)).delete()
    AnchorResponseReaction.objects.filter(Q(user=user) | Q(response__user=user)).delete()
    CirclePostEngagement.objects.filter(Q(user=user) | Q(post__user=user)).delete()
    CirclePostCommentLike.objects.filter(Q(user=user) | Q(comment__user=user)).delete()

    AnchorResponse.objects.filter(user=user, deleted_at__isnull=True).update(
        content="",
        media_url="",
        media_type="",
        deleted_at=now,
    )
    CirclePostComment.objects.filter(user=user, deleted_at__isnull=True).update(
        text="",
        deleted_at=now,
    )
    CirclePost.objects.filter(user=user, deleted_at__isnull=True).update(
        text="",
        image_url="",
        media_url="",
        deleted_at=now,
    )
    Comment.all_objects.filter(user=user, deleted_at__isnull=True).update(
        text="",
        mentioned_users=[],
        deleted_at=now,
    )
    Post.all_objects.filter(user=user, deleted_at__isnull=True).update(
        caption="",
        media_count=0,
        deleted_at=now,
    )
    Anchor.objects.filter(created_by=user, deleted_at__isnull=True).update(
        content="",
        media_url="",
        anchor_image="",
        anchor_video="",
        anchor_thumbnail="",
        background_image="",
        anchor_text="",
        anchor_verse="",
        anchor_image_text="",
        deleted_at=now,
    )

    Circle.objects.filter(created_by=user).update(created_by=None)
    Anchor.objects.filter(created_by=user).update(created_by=None)

    CircleReport.objects.filter(reporter=user).delete()
    CircleReport.objects.filter(resolved_by=user).update(resolved_by=None)
    Report.objects.filter(reporter=user).delete()
    Report.objects.filter(reviewed_by=user).update(reviewed_by=None)

    Notification.objects.filter(Q(user=user) | Q(sender=user)).delete()
    NotificationPreference.objects.filter(user=user).delete()
    MediaFile.objects.filter(user=user).delete()


def anonymize_user_for_permanent_delete(user, now: datetime) -> None:
    """Permanently disable and anonymize a user row without hard-deleting it."""
    user_id = str(user.id)
    id_token = user.id.hex if hasattr(user.id, "hex") else user_id.replace("-", "")

    user.email = f"deleted-{id_token}@deleted.ziona.local"[:255]
    user.username = f"deleted_{id_token[:22]}"
    user.full_name = ""
    user.bio = ""
    user.bio_link = ""
    user.avatar_url = ""
    user.role = UserRole.USER
    user.is_email_verified = False
    user.needs_username_selection = False
    user.hide_like_count = False
    user.encrypted_dob = None
    user.location = ""
    user.status = UserStatus.ACTIVE
    user.warned_at = None
    user.suspended_at = None
    user.suspension_reason = ""
    user.is_active = False
    user.is_staff = False
    user.deleted_at = now
    user.last_login_ip = None
    user.auth_provider = "email"
    user.firebase_uid = None
    user.social_auth_provider = None
    user.google_id = None
    user.set_unusable_password()
    user.save(
        update_fields=[
            "email",
            "username",
            "full_name",
            "bio",
            "bio_link",
            "avatar_url",
            "role",
            "is_email_verified",
            "needs_username_selection",
            "hide_like_count",
            "encrypted_dob",
            "location",
            "status",
            "warned_at",
            "suspended_at",
            "suspension_reason",
            "is_active",
            "is_staff",
            "deleted_at",
            "last_login_ip",
            "auth_provider",
            "firebase_uid",
            "social_auth_provider",
            "google_id",
            "password",
            "updated_at",
        ]
    )

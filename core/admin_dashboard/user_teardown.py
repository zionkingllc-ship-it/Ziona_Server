"""Destructive user-data teardown used by admin permanent deletion.

Split from core/admin_dashboard/user_services.py (no behavior change).
"""

import logging

from django.db.models import Q

logger = logging.getLogger("core.admin_dashboard")


def _revoke_user_sessions(user_id, delete_device_tokens: bool) -> None:
    from core.authentication.tokens import TokenService
    from core.notifications.models import DeviceToken

    TokenService.revoke_all_user_tokens(str(user_id))
    tokens = DeviceToken.objects.filter(user_id=user_id)
    if delete_device_tokens:
        tokens.delete()
    else:
        tokens.update(is_active=False)


def _remove_or_hide_user_data(user, now) -> None:
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
    from core.donations.models import Donation, SupporterIdentity
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

    # Donations: scrub identifying PII (email/name) but KEEP the financial record
    # (amount, currency, Stripe IDs, timestamps) for accounting/tax retention.
    # The user row is anonymized in place, so the SET_NULL FKs never fire.
    identity = SupporterIdentity.objects.filter(user=user).first()
    donation_filter = Q(user=user)
    if identity is not None:
        donation_filter |= Q(supporter_identity=identity)
    Donation.objects.filter(donation_filter).update(donor_name="", donor_email="")
    if identity is not None:
        identity.normalized_email = f"deleted-{identity.id.hex}@deleted.ziona.local"
        identity.contact_email = ""
        identity.display_name = ""
        identity.save(
            update_fields=["normalized_email", "contact_email", "display_name", "updated_at"]
        )

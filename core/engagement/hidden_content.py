"""Hidden-content helpers shared across engagement, moderation, and circles."""

from __future__ import annotations

from django.db.models import Exists, OuterRef

from core.engagement.cache import EngagementCache
from core.engagement.models import Comment, HiddenComment, HiddenPost
from core.posts.models import Post
from core.shared.exceptions import EngagementError, ErrorCode

HIDDEN_CONTENT_LIMIT = 1000


def exclude_hidden_posts(queryset, user_id: str | None, *, target_field: str = "pk"):
    """Exclude posts the viewer has hidden using a NOT EXISTS subquery."""
    if not user_id:
        return queryset

    hidden_subquery = Exists(
        HiddenPost.objects.filter(user_id=user_id, post_id=OuterRef(target_field))
    )
    return queryset.annotate(_is_hidden_post=hidden_subquery).filter(_is_hidden_post=False)


def exclude_hidden_comments(queryset, user_id: str | None, *, target_field: str = "pk"):
    """Exclude comments the viewer has hidden using a NOT EXISTS subquery."""
    if not user_id:
        return queryset

    hidden_subquery = Exists(
        HiddenComment.objects.filter(user_id=user_id, comment_id=OuterRef(target_field))
    )
    return queryset.annotate(_is_hidden_comment=hidden_subquery).filter(_is_hidden_comment=False)


def exclude_hidden_circle_content(
    queryset,
    user_id: str | None,
    *,
    target_type: str,
    target_field: str = "pk",
):
    """Exclude circle content hidden by the viewer using a NOT EXISTS subquery."""
    if not user_id:
        return queryset

    from core.circles.models import HiddenCircleContent

    hidden_subquery = Exists(
        HiddenCircleContent.objects.filter(
            user_id=user_id,
            target_type=target_type,
            target_id=OuterRef(target_field),
        )
    )
    annotation_name = f"_is_hidden_circle_{target_type}"
    return queryset.annotate(**{annotation_name: hidden_subquery}).filter(
        **{annotation_name: False}
    )


def hide_post_for_user(user_id: str, post_id: str) -> bool:
    """Hide a post for a specific viewer with a 1,000-item sliding window."""
    post = Post.objects.filter(id=post_id, deleted_at__isnull=True).first()
    if not post:
        raise EngagementError("Post not found.", ErrorCode.POST_NOT_FOUND)

    hidden_post, created = HiddenPost.objects.get_or_create(user_id=user_id, post_id=post_id)
    EngagementCache.mark_post_hidden(user_id, str(post_id))

    if created:
        _trim_hidden_rows(
            HiddenPost,
            user_id,
            on_evict=lambda stale: EngagementCache.unmark_post_hidden(
                user_id,
                str(stale.post_id),
            ),
        )

    return True


def unhide_post_for_user(user_id: str, post_id: str) -> bool:
    """Unhide a previously hidden post for a viewer."""
    deleted, _ = HiddenPost.objects.filter(user_id=user_id, post_id=post_id).delete()
    if deleted:
        EngagementCache.unmark_post_hidden(user_id, str(post_id))
        return True
    return False


def hide_comment_for_user(user_id: str, comment_id: str) -> bool:
    """Hide a comment for a specific viewer with a 1,000-item sliding window."""
    comment = Comment.objects.filter(id=comment_id, deleted_at__isnull=True).first()
    if not comment:
        raise EngagementError("Comment not found.", ErrorCode.COMMENT_NOT_FOUND)

    hidden_comment, created = HiddenComment.objects.get_or_create(
        user_id=user_id,
        comment_id=comment_id,
    )
    EngagementCache.mark_comment_hidden(user_id, str(comment_id))

    if created:
        _trim_hidden_rows(
            HiddenComment,
            user_id,
            on_evict=lambda stale: EngagementCache.unmark_comment_hidden(
                user_id,
                str(stale.comment_id),
            ),
        )

    return True


def hide_circle_content_for_user(user_id: str, target_type: str, target_id: str) -> bool:
    """Hide reported circle content for a viewer with a 1,000-item sliding window."""
    from core.circles.models import HiddenCircleContent

    _, created = HiddenCircleContent.objects.get_or_create(
        user_id=user_id,
        target_type=target_type,
        target_id=target_id,
    )
    EngagementCache.mark_circle_content_hidden(user_id, target_type, str(target_id))

    if created:
        _trim_hidden_rows(
            HiddenCircleContent,
            user_id,
            on_evict=lambda stale: EngagementCache.unmark_circle_content_hidden(
                user_id,
                stale.target_type,
                str(stale.target_id),
            ),
        )

    return True


def _trim_hidden_rows(model, user_id: str, *, on_evict) -> None:
    """Keep only the newest 1,000 hidden rows for a viewer."""
    overflow = max(model.objects.filter(user_id=user_id).count() - HIDDEN_CONTENT_LIMIT, 0)
    if overflow == 0:
        return

    stale_rows = list(model.objects.filter(user_id=user_id).order_by("created_at", "id")[:overflow])
    for stale_row in stale_rows:
        on_evict(stale_row)
        stale_row.delete()

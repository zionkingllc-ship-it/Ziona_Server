"""
Circle Post Comment Service Layer.

Handles CRUD and like-toggling for CirclePostComments.
Mirrors the architecture of response_services.py for consistency.

All counter updates use F() expressions so concurrent requests never
create race conditions on the denormalized counts.
"""
import logging

from django.db import IntegrityError, transaction
from django.db.models import Exists, F, OuterRef
from django.utils import timezone

from core.circles.models import (
    CirclePost,
    CirclePostComment,
    CirclePostCommentLike,
)
from core.shared.exceptions import ZionaError

logger = logging.getLogger("core.circles")

# ── Error codes ────────────────────────────────────────────────────────────────
POST_NOT_FOUND = "CIRCLE_POST_NOT_FOUND"
COMMENT_NOT_FOUND = "CIRCLE_COMMENT_NOT_FOUND"
NOT_COMMENT_AUTHOR = "NOT_COMMENT_AUTHOR"
EMPTY_COMMENT = "EMPTY_COMMENT"


# ── Queries ────────────────────────────────────────────────────────────────────


def get_circle_post_comments(
    post_id: str,
    viewer_id: str | None = None,
    page: int = 1,
    page_size: int = 30,
) -> tuple[list[CirclePostComment], bool, int]:
    """Fetch paginated, non-deleted comments for a CirclePost.

    Annotates each comment with:
    - is_liked_by_viewer  — so the mobile app can render the heart icon state.
    - author (via select_related) — so the resolver needs zero extra queries.

    Returns:
        (comments, has_next_page, total_count)
    """
    try:
        post = CirclePost.objects.get(id=post_id, deleted_at__isnull=True)
    except CirclePost.DoesNotExist:
        raise ZionaError(message="Post not found", code=POST_NOT_FOUND) from None

    queryset = (
        CirclePostComment.objects.filter(post=post, deleted_at__isnull=True)
        .select_related("user")
        .order_by("created_at")  # oldest-first for threaded reading
    )

    # Annotate viewer like state in a single pass — zero N+1.
    if viewer_id:
        queryset = queryset.annotate(
            is_liked_by_viewer=Exists(
                CirclePostCommentLike.objects.filter(
                    comment=OuterRef("pk"),
                    user_id=viewer_id,
                )
            )
        )
    else:
        queryset = queryset.annotate(
            is_liked_by_viewer=Exists(CirclePostCommentLike.objects.none())
        )

    total_count = queryset.count()
    offset = (page - 1) * page_size
    comments = list(queryset[offset : offset + page_size + 1])

    has_next_page = len(comments) > page_size
    return comments[:page_size], has_next_page, total_count


# ── Mutations ──────────────────────────────────────────────────────────────────


@transaction.atomic
def create_circle_post_comment(
    user_id: str,
    post_id: str,
    text: str,
) -> CirclePostComment:
    """Create an inline comment on a CirclePost.

    Atomically increments CirclePost.comments_count via F() to avoid
    read-modify-write races under concurrent comment creation.
    """
    text = text.strip()
    if not text:
        raise ZionaError(message="Comment text cannot be empty.", code=EMPTY_COMMENT)

    try:
        post = CirclePost.objects.select_for_update().get(id=post_id, deleted_at__isnull=True)
    except CirclePost.DoesNotExist:
        raise ZionaError(message="Post not found", code=POST_NOT_FOUND) from None

    comment = CirclePostComment.objects.create(
        post=post,
        user_id=user_id,
        text=text,
    )

    # Atomically increment the post's comment counter.
    CirclePost.objects.filter(pk=post.pk).update(comments_count=F("comments_count") + 1)

    logger.info(
        "circle_post_comment_created",
        extra={"comment_id": str(comment.id), "post_id": post_id, "user_id": user_id},
    )
    return comment


@transaction.atomic
def delete_circle_post_comment(
    user_id: str,
    comment_id: str,
) -> bool:
    """Soft-delete a comment if the requesting user is its author.

    Atomically decrements CirclePost.comments_count.
    """
    try:
        comment = CirclePostComment.objects.select_for_update().get(
            id=comment_id, deleted_at__isnull=True
        )
    except CirclePostComment.DoesNotExist:
        raise ZionaError(message="Comment not found", code=COMMENT_NOT_FOUND) from None

    if str(comment.user_id) != str(user_id):
        raise ZionaError(message="You can only delete your own comments", code=NOT_COMMENT_AUTHOR)

    comment.deleted_at = timezone.now()
    comment.save(update_fields=["deleted_at", "updated_at"])

    # Atomically decrement the post's comment counter (floor at 0).
    CirclePost.objects.filter(pk=comment.post_id, comments_count__gt=0).update(
        comments_count=F("comments_count") - 1
    )

    logger.info(
        "circle_post_comment_deleted",
        extra={"comment_id": comment_id, "user_id": user_id},
    )
    return True


@transaction.atomic
def toggle_circle_post_comment_like(
    user_id: str,
    comment_id: str,
) -> tuple[bool, int]:
    """Toggle a like on a CirclePostComment.

    Returns:
        (liked: bool, new_likes_count: int)
        liked=True  → like was created.
        liked=False → like was removed.
    """
    try:
        comment = CirclePostComment.objects.select_for_update().get(
            id=comment_id, deleted_at__isnull=True
        )
    except CirclePostComment.DoesNotExist:
        raise ZionaError(message="Comment not found", code=COMMENT_NOT_FOUND) from None

    try:
        # Attempt to create the like row.
        CirclePostCommentLike.objects.create(comment=comment, user_id=user_id)
        # Atomically increment likes_count.
        CirclePostComment.objects.filter(pk=comment.pk).update(likes_count=F("likes_count") + 1)
        comment.refresh_from_db(fields=["likes_count"])
        return True, comment.likes_count

    except IntegrityError:
        # Like already exists — this is an unlike (toggle off).
        CirclePostCommentLike.objects.filter(comment=comment, user_id=user_id).delete()
        # Atomically decrement (floor at 0).
        CirclePostComment.objects.filter(pk=comment.pk, likes_count__gt=0).update(
            likes_count=F("likes_count") - 1
        )
        comment.refresh_from_db(fields=["likes_count"])
        return False, comment.likes_count

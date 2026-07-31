"""Ordered reads for a post's media, preserving the creator's selected order.

`Post.media_files` / `CirclePost.media_files` use a `through` model carrying a
`position` column. `.all()` on the M2M does NOT honour that column by default
(it orders by MediaFile's own Meta), so reads must annotate/order by it here.

`order` is annotated onto each MediaFile because `PostService._build_post_dto`
already sorts by (and emits) `getattr(item, "order", …)` — so an annotated
`order` makes both the sort and the DTO's `order` field correct with no change
to the DTO builder. Django reuses the M2M's existing through join for this
annotation (verified), so no extra join or N+1 is introduced.
"""

from django.db.models import F, Prefetch, QuerySet

from core.media.models import MediaFile

# Reverse relation name from MediaFile to each through model (see models.py).
POST_MEDIA_POSITION = "post_media_through__position"
CIRCLE_POST_MEDIA_POSITION = "circle_post_media_through__position"


def order_media_by_position(media_qs: QuerySet, position_path: str) -> QuerySet:
    """Annotate `order` from the through `position` and sort by it."""
    return media_qs.annotate(order=F(position_path)).order_by("order")


def ordered_post_media_prefetch(lookup: str = "media_files") -> Prefetch:
    """Prefetch `Post.media_files` in selected order (annotated with `order`).

    `lookup` allows nested paths, e.g. "post__media_files".
    """
    return Prefetch(
        lookup,
        queryset=order_media_by_position(MediaFile.objects.all(), POST_MEDIA_POSITION),
    )


def ordered_circle_post_media_prefetch(lookup: str = "media_files") -> Prefetch:
    """Prefetch `CirclePost.media_files` in selected order (annotated with `order`)."""
    return Prefetch(
        lookup,
        queryset=order_media_by_position(MediaFile.objects.all(), CIRCLE_POST_MEDIA_POSITION),
    )

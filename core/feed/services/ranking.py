"""Feed ranking — base querysets, engagement scoring, affinity.

Split from the former core/feed/services.py (no behavior change).
"""

import logging
from datetime import timedelta

from django.db.models import (
    Case,
    Count,
    ExpressionWrapper,
    F,
    FloatField,
    IntegerField,
    OuterRef,
    Q,
    Subquery,
    Value,
    When,
)
from django.db.models.functions import Coalesce
from django.utils import timezone

from core.engagement.hidden_content import exclude_hidden_posts
from core.posts.models import Post

logger = logging.getLogger("core.feed")

NEW_USER_THRESHOLD_DAYS = 7
DEFAULT_FEED_LIMIT = 20
FEED_CACHE_TTL = 300

# Celebrity threshold — must match the value in tasks.py.
CELEBRITY_FOLLOWER_THRESHOLD = 50_000

# Maximum number of IDs to read from a Redis inbox in a single LRANGE.
_INBOX_READ_LIMIT = 60

DISCOVERY_BLEND_SIZE = 3
FOLLOWED_BLEND_SIZE = 1
REPORT_PENALTY_THRESHOLD = 5
REPORT_SUPPRESSION_THRESHOLD = 10
CREATOR_DIVERSITY_WINDOW = 10
CREATOR_DIVERSITY_MAX_PER_WINDOW = 2


def _exclude_hidden_posts(qs, user_id: str | None):
    """Exclude posts hidden by the user using a performant NOT EXISTS subquery."""
    return exclude_hidden_posts(qs, user_id)


def _base_post_queryset():
    """Common feed queryset with related objects needed for feed DTO hydration."""
    return (
        Post.objects.select_related("user")
        .prefetch_related("media_files", "post_media")
        .filter(deleted_at__isnull=True)
    )


def _with_engagement_counts(qs):
    """Annotate reusable engagement counts for feed ranking and DTO stats."""
    return qs.annotate(
        likes_count=Count("likes", distinct=True),
        comments_count=Count(
            "comments",
            filter=Q(comments__deleted_at__isnull=True),
            distinct=True,
        ),
        shares_count=Count("shares", distinct=True),
        saves_count=Count("saves", distinct=True),
        unique_reports_count=Count("reports__reporter_id", distinct=True),
    )


def _with_final_score(qs):
    """Annotate MVP ranking score: engagement × freshness × report penalty."""
    now = timezone.now()
    return _with_engagement_counts(qs).annotate(
        engagement_score=ExpressionWrapper(
            F("likes_count") + (F("comments_count") * Value(2)) + (F("shares_count") * Value(3)),
            output_field=FloatField(),
        ),
        freshness_multiplier=Case(
            When(created_at__gte=now - timedelta(days=1), then=Value(1.0)),
            When(created_at__gte=now - timedelta(days=3), then=Value(0.7)),
            When(created_at__gte=now - timedelta(days=7), then=Value(0.4)),
            default=Value(0.1),
            output_field=FloatField(),
        ),
        report_penalty_multiplier=Case(
            When(unique_reports_count__gte=REPORT_PENALTY_THRESHOLD, then=Value(0.5)),
            default=Value(1.0),
            output_field=FloatField(),
        ),
        final_score=ExpressionWrapper(
            F("engagement_score") * F("freshness_multiplier") * F("report_penalty_multiplier"),
            output_field=FloatField(),
        ),
    )


def _ranked_queryset():
    """Base algorithmic feed queryset ordered by score, freshness, and stable ID tie-breaker."""
    return (
        _with_final_score(_base_post_queryset())
        .filter(unique_reports_count__lt=REPORT_SUPPRESSION_THRESHOLD)
        .order_by("-final_score", "-created_at", "-id")
    )


def _with_creator_affinity(qs, user_id: str):
    """Annotate likes this viewer gave each creator in the last 30 days."""
    from core.engagement.models import Like

    cutoff = timezone.now() - timedelta(days=30)
    affinity = (
        Like.objects.filter(
            user_id=user_id,
            post__user_id=OuterRef("user_id"),
            created_at__gte=cutoff,
        )
        .values("post__user_id")
        .annotate(total=Count("id"))
        .values("total")[:1]
    )
    return qs.annotate(
        creator_affinity=Coalesce(
            Subquery(affinity, output_field=IntegerField()),
            Value(0),
        )
    )

"""Feed cursors — opaque cursor codec + per-algorithm apply helpers.

Split from the former core/feed/services.py (no behavior change).
"""

import base64
import json
import logging

from django.db.models import (
    Q,
)
from django.utils.dateparse import parse_datetime

from core.posts.models import Post

logger = logging.getLogger("core.feed")


class FeedCursor:
    """Opaque cursor that encodes feed pagination state as Base64 JSON.

    Format::

        base64({"v":1, "id":"<post_uuid>", "algo":"<algo_tag>",
                "ts":"<iso8601>", "tier":<int|null>})

    Backward-compatible: if decoding fails (e.g. the value is a raw UUID
    from an older mobile client) the cursor is treated as a legacy value
    and the ``algo`` / ``tier`` fields default to ``None``.
    """

    VERSION = 2

    @staticmethod
    def encode(
        post_id: str,
        algo: str,
        created_at,
        *,
        tier: int | None = None,
        score: float | None = None,
        **extra,
    ) -> str:
        """Encode pagination state into an opaque cursor string."""
        payload = {
            "v": FeedCursor.VERSION,
            "id": str(post_id),
            "algo": algo,
            "ts": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at),
        }
        if tier is not None:
            payload["tier"] = tier
        if score is not None:
            payload["score"] = float(score)
        if extra:
            payload.update(extra)
        return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()

    @staticmethod
    def decode(cursor: str) -> dict:
        """Decode an opaque cursor, falling back to legacy UUID format."""
        if not cursor:
            return {}
        try:
            raw = base64.urlsafe_b64decode(cursor.encode()).decode()
            data = json.loads(raw)
            if data.get("v") in (1, FeedCursor.VERSION):
                return data
        except Exception:
            logger.debug("invalid cursor format — falling back to legacy uuid")
        # Backward-compatible: treat as raw post UUID from older clients.
        return {"id": cursor, "algo": None, "ts": None, "tier": None}


def _ranked_cursor_payload(post, fallback: dict | None = None) -> dict:
    """Build a nested ranked cursor payload, preserving prior state if no post was used."""
    if not post:
        return fallback or {}
    return {
        "id": str(post.id),
        "score": float(getattr(post, "final_score", 0) or 0),
        "ts": post.created_at.isoformat(),
    }


def _chronological_cursor_payload(post, fallback: dict | None = None) -> dict:
    """Build a nested chronological cursor payload, preserving prior state if no post was used."""
    if not post:
        return fallback or {}
    return {
        "id": str(post.id),
        "ts": post.created_at.isoformat(),
    }


def _apply_cursor(qs, cursor_post_id: str):
    """
    Apply compound (created_at, id) keyset pagination to a queryset.

    Using only ``created_at__lt`` is unsafe when multiple posts share the
    same timestamp (common in tests and rapid mobile submissions): those
    posts get silently skipped on the next page. The compound keyset
    filter handles ties correctly:

        page N+1 = posts where
            (created_at < cursor.created_at)
            OR
            (created_at == cursor.created_at AND id < cursor.id)

    This requires the queryset to be ordered by ``(-created_at, -id)``.
    The cursor value is still just the post UUID (backward-compatible).
    """
    try:
        cursor_post = Post.objects.filter(id=cursor_post_id).values("created_at", "id").first()
        if cursor_post:
            qs = qs.filter(
                Q(created_at__lt=cursor_post["created_at"])
                | Q(
                    created_at=cursor_post["created_at"],
                    id__lt=cursor_post["id"],
                )
            )
    except Exception:  # noqa: BLE001
        # If the cursor ID is invalid/deleted, ignore it and return the
        # unfiltered queryset (effectively a first-page fallback).
        logger.debug("Failed to apply feed cursor: invalid cursor_id")
    return qs


def _apply_ranked_cursor(qs, cursor_data: dict, fallback_cursor: str | None = None):
    """Apply keyset pagination for final_score DESC, created_at DESC, id DESC."""
    cursor_id = cursor_data.get("id") or fallback_cursor
    cursor_score = cursor_data.get("score")
    cursor_ts = parse_datetime(cursor_data.get("ts", "") or "")

    if cursor_id and cursor_score is not None and cursor_ts is not None:
        try:
            cursor_score = float(cursor_score)
            return qs.filter(
                Q(final_score__lt=cursor_score)
                | Q(final_score=cursor_score, created_at__lt=cursor_ts)
                | Q(
                    final_score=cursor_score,
                    created_at=cursor_ts,
                    id__lt=cursor_id,
                )
            )
        except (TypeError, ValueError):
            logger.debug("Failed to apply ranked feed cursor: invalid score")

    if cursor_id:
        return _apply_cursor(qs, cursor_id)
    return qs


def _apply_chronological_affinity_cursor(qs, cursor_data: dict, fallback_cursor: str | None = None):
    """Apply keyset pagination for created_at DESC, affinity DESC, id DESC."""
    cursor_id = cursor_data.get("id") or fallback_cursor
    cursor_affinity = cursor_data.get("affinity")
    cursor_ts = parse_datetime(cursor_data.get("ts", "") or "")

    if cursor_id and cursor_affinity is not None and cursor_ts is not None:
        try:
            cursor_affinity = int(cursor_affinity)
            return qs.filter(
                Q(created_at__lt=cursor_ts)
                | Q(created_at=cursor_ts, creator_affinity__lt=cursor_affinity)
                | Q(
                    created_at=cursor_ts,
                    creator_affinity=cursor_affinity,
                    id__lt=cursor_id,
                )
            )
        except (TypeError, ValueError):
            logger.debug("Failed to apply following cursor: invalid affinity")

    if cursor_id:
        return _apply_cursor(qs, cursor_id)
    return qs


def _apply_following_cursor(
    qs,
    cursor_post_id: str,
    following_ids: list | set,
    *,
    cursor_tier: int | None = None,
):
    """
    Compound (is_following, created_at, id) keyset cursor for _returning_user_feed.

    The regular _apply_cursor only handles (created_at, id) which silently drops
    discovery posts that are newer than the cursor's followed-author post.

    **Tier-flip fix (Phase 2):**  If the opaque cursor carries the tier value
    that was computed when the *previous* page was served, we use that frozen
    tier value instead of re-deriving it from the current follow graph.  This
    prevents the "tier flip" bug where a follow/unfollow between pages caused
    massive content jumps.
    """
    try:
        cursor_post = (
            Post.objects.filter(id=cursor_post_id).values("created_at", "id", "user_id").first()
        )
        if cursor_post:
            # Use the tier frozen in the cursor if available (Phase 2 opaque
            # cursor).  Fall back to live derivation for legacy UUID cursors.
            if cursor_tier is not None:
                frozen_tier = cursor_tier
            else:
                frozen_tier = (
                    1 if str(cursor_post["user_id"]) in {str(fid) for fid in following_ids} else 0
                )

            qs = qs.filter(
                Q(is_following__lt=frozen_tier)
                | Q(
                    is_following=frozen_tier,
                    created_at__lt=cursor_post["created_at"],
                )
                | Q(
                    is_following=frozen_tier,
                    created_at=cursor_post["created_at"],
                    id__lt=cursor_post["id"],
                )
            )
    except Exception:  # noqa: BLE001
        logger.debug("Failed to apply following feed cursor: invalid cursor_id")
    return qs

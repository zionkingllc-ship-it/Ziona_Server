"""Circles services package.

Re-exports the full public surface of the former core/circles/services.py
module so every existing import path keeps working (no behavior change).
"""

from core.circles.services.anchor_engagement import like_anchor, pray_for_anchor
from core.circles.services.circle_posts import (
    create_circle_post,
    ensure_circle_post_liked,
    get_circle_feed,
    get_circle_post,
    like_circle_post,
    pray_for_circle_post,
)
from core.circles.services.membership import (
    create_circle,
    get_all_circles,
    get_circle_by_id,
    get_my_circles,
    get_suggested_circles,
    join_circle,
    leave_circle,
)

__all__ = [
    "create_circle",
    "create_circle_post",
    "ensure_circle_post_liked",
    "get_all_circles",
    "get_circle_by_id",
    "get_circle_feed",
    "get_circle_post",
    "get_my_circles",
    "get_suggested_circles",
    "join_circle",
    "leave_circle",
    "like_anchor",
    "like_circle_post",
    "pray_for_anchor",
    "pray_for_circle_post",
]

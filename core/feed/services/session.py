"""For You feed sessions: the unit of shuffling and of "already shown".

A session starts when the app asks for a first page (no cursor) and covers every
page reached through ``next_cursor``. Everything that varies from one open to the
next is fixed for the session and carried in the cursor, so a session has one
deterministic order that keyset pagination can walk without repeats or gaps:

- the shuffle parameters (``multiplier``, ``offset``), and
- ``started_at``, which freezes the "already shown" set: only posts served
  *before* the session began are demoted. Posts this session serves are recorded
  too — stamped no earlier than its start — so they cannot reorder the session
  they belong to, even if two servers' clocks disagree slightly.
"""

from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache

from core.shared.utils import parse_uuid

logger = logging.getLogger("core.feed")

# A session older than this starts afresh rather than continuing. It also bounds
# how long served-history entries outlive the "already shown" window, so a
# session's frozen view of that history cannot change underneath it.
SESSION_MAX_AGE_SECONDS = 6 * 60 * 60

# Shuffle multiplier bounds — see ranking._with_feed_score. Large enough that
# frac(shuffle_key * multiplier + offset) behaves as a fresh permutation per
# session, small enough to keep double-precision headroom.
_MULTIPLIER_MIN = 10_000
_MULTIPLIER_MAX = 100_000

# Entries carried from earlier sessions — trimmed to this when a session starts.
# Covers ~15 pages of 20 a day and bounds the cached value (~12 KB).
SERVED_HISTORY_MAX = 300
# Entries one session may add before its own oldest are dropped. Only the
# session's own entries are ever capped mid-session; see ServedHistory.
SESSION_HISTORY_MAX = 500
_SERVED_KEY = "feed:served:v2:{user_id}"


@dataclass(frozen=True)
class FeedSession:
    multiplier: int
    offset: float
    started_at: float  # epoch seconds, microsecond precision

    @classmethod
    def start(cls) -> FeedSession:
        return cls(
            multiplier=_MULTIPLIER_MIN + secrets.randbelow(_MULTIPLIER_MAX - _MULTIPLIER_MIN),
            offset=secrets.randbelow(10**9) / 10**9,
            started_at=_now(),
        )

    @classmethod
    def from_payload(cls, payload) -> FeedSession | None:
        """Rebuild a session from a cursor, or None if absent, stale or malformed.

        The cursor comes from the client, so every field is validated. A bad
        value only ever costs the caller their own session: a fresh one starts.
        """
        if not isinstance(payload, dict):
            return None
        try:
            multiplier = int(payload["m"])
            offset = float(payload["o"])
            started_at = float(payload["t"])
        except (KeyError, TypeError, ValueError):
            return None
        now = time.time()
        if not (
            _MULTIPLIER_MIN <= multiplier < _MULTIPLIER_MAX
            and 0.0 <= offset < 1.0
            and now - SESSION_MAX_AGE_SECONDS <= started_at <= now + 60
        ):
            return None
        return cls(multiplier=multiplier, offset=offset, started_at=started_at)

    def to_payload(self) -> dict:
        return {"m": self.multiplier, "o": self.offset, "t": self.started_at}


def _now() -> float:
    # Microseconds, not whole seconds: a reopen within the same second as the
    # previous page must still see that page as already shown.
    return round(time.time(), 6)


def seen_window_seconds() -> int:
    return max(0, int(settings.FEED_SEEN_WINDOW_HOURS)) * 60 * 60


class ServedHistory:
    """Posts recently served to one user, split so a session's view never moves.

    - ``base``: ``{post_id: served_at}`` from sessions before the current one.
      A session's "already shown" set is read from it, and it does not change
      while that session runs — so neither does the session's order.
    - ``own``: what the current session has served. It is folded into ``base``
      only when the next session starts, which is also the only time ``base`` is
      trimmed. Trimming or re-stamping mid-session would change the frozen set:
      posts would lose their demotion, jump above the cursor and be skipped.

    Stored as a single cache value — one read and one write per feed page — so it
    costs the same Upstash commands as a sorted set while running unchanged on
    any cache backend. A failing cache never fails the feed: the history reads as
    empty and the write is skipped.
    """

    def __init__(self, user_id: str, base: dict, own: dict, session_key: float | None):
        self.user_id = str(user_id)
        self._base = base
        self._own = own
        self._session_key = session_key

    @classmethod
    def load(cls, user_id: str, session: FeedSession) -> ServedHistory:
        try:
            stored = cache.get(_SERVED_KEY.format(user_id=user_id))
        except Exception:  # noqa: BLE001 — a cache outage must not break the feed
            logger.warning("feed_served_history_read_failed", exc_info=True)
            stored = None
        if not isinstance(stored, dict):
            stored = {}
        base = stored.get("base") if isinstance(stored.get("base"), dict) else {}
        own = stored.get("own") if isinstance(stored.get("own"), dict) else {}

        if stored.get("session") != session.started_at:
            # Another session's pages become history for this one.
            for post_id, served_at in own.items():
                base[post_id] = max(served_at, base.get(post_id, served_at))
            if len(base) > SERVED_HISTORY_MAX:
                newest = sorted(base.items(), key=lambda item: item[1], reverse=True)
                base = dict(newest[:SERVED_HISTORY_MAX])
            own = {}
        return cls(user_id, base, own, session.started_at)

    def served_before(self, session: FeedSession) -> list[str]:
        """Ids served within the seen window before ``session`` began."""
        window_start = session.started_at - seen_window_seconds()
        return [
            post_id
            for post_id, served_at in self._base.items()
            if window_start <= served_at < session.started_at
        ]

    def record(self, post_ids, session: FeedSession) -> None:
        """Remember ``post_ids`` as served now, within ``session``.

        Stamped no earlier than the session's start, so a session's own pages
        can never count against it even if two servers' clocks disagree.
        """
        now = max(_now(), session.started_at)
        retention = seen_window_seconds() + SESSION_MAX_AGE_SECONDS
        # Safe mid-session: anything this old predates the session's seen window
        # (sessions last at most SESSION_MAX_AGE_SECONDS).
        base = {
            post_id: served_at
            for post_id, served_at in self._base.items()
            if served_at >= now - retention
        }
        own = dict(self._own)
        for post_id in post_ids:
            if parse_uuid(str(post_id)) is not None:
                own[str(post_id)] = now
        if len(own) > SESSION_HISTORY_MAX:
            newest = sorted(own.items(), key=lambda item: item[1], reverse=True)
            own = dict(newest[:SESSION_HISTORY_MAX])
        value = {"base": base, "own": own, "session": self._session_key}
        try:
            cache.set(_SERVED_KEY.format(user_id=self.user_id), value, timeout=retention)
        except Exception:  # noqa: BLE001
            logger.warning("feed_served_history_write_failed", exc_info=True)
            return
        self._base, self._own = base, own

"""Page assembly: apply variety rules without repeating or losing posts.

Candidates arrive in session rank order. The variety rules — creator spacing and
media-kind runs — may hold a post back, but a held-back post must neither vanish
nor reappear. The old approach skipped such posts and set the next cursor from
the page's *last* post; when skipped posts were back-filled onto the end of a
page, that last post was no longer its lowest-ranked one, so the next page
repeated posts — and skipped posts that never fit were lost for the session.

Here every candidate the page looks at is *consumed*: it is either shown, or
carried in the cursor and offered first on the next page. Consumption follows
rank order, so the consumed candidates are always a prefix of the stream, and
the next page continues strictly after that prefix. Shown + carried + not yet
consumed partitions the stream: nothing repeats, nothing is dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.conf import settings

from core.feed.services.ranking import (
    CREATOR_DIVERSITY_MAX_PER_WINDOW,
    CREATOR_DIVERSITY_WINDOW,
)

# Most held-back posts carried between pages. Bounds the cursor size; once
# reached, the highest-ranked held-back post is shown even though it breaks a
# variety rule (as it would be when nothing else is left to show).
MAX_CARRY = 12


@dataclass
class AssembledPage:
    posts: list
    consumed: int  # length of the prefix of ``fresh`` this page used
    carry: list = field(default_factory=list)  # consumed but not shown


def _max_kind_run() -> int:
    return max(0, int(settings.FEED_MAX_SAME_KIND_RUN))


def _fits(page: list, post, max_kind_run: int) -> bool:
    author = str(post.user_id)
    if page and str(page[-1].user_id) == author:
        return False
    recent = page[-(CREATOR_DIVERSITY_WINDOW - 1) :]
    if sum(1 for item in recent if str(item.user_id) == author) >= (
        CREATOR_DIVERSITY_MAX_PER_WINDOW
    ):
        return False
    if max_kind_run and len(page) >= max_kind_run:
        tail = page[-max_kind_run:]
        if all(item.post_type == post.post_type for item in tail):
            return False
    return True


def assemble_page(
    carried: list, fresh: list, limit: int, *, vary_kinds: bool = True
) -> AssembledPage:
    """Fill a page from ``carried`` (offered first) then ``fresh``, in rank order.

    Each slot takes the highest-ranked post that fits the variety rules. When
    nothing fits and no more candidates can be consumed (the carry is full or
    the candidates are exhausted), the rules relax in order of importance: first
    the media-kind rule — creator spacing still holds — and only then all of
    them. A full page beats a perfectly varied short one.

    ``vary_kinds=False`` drops the media-kind rule, for listings already
    filtered to one kind, where it could never be met.
    """
    max_kind_run = _max_kind_run() if vary_kinds else 0
    page: list = []
    waiting: list = list(carried)
    consumed = 0

    def take(rule_kind_run: int) -> bool:
        choice = next((post for post in waiting if _fits(page, post, rule_kind_run)), None)
        if choice is None:
            return False
        waiting.remove(choice)
        page.append(choice)
        return True

    while len(page) < limit:
        if take(max_kind_run):
            continue
        if consumed < len(fresh) and len(waiting) < MAX_CARRY:
            post = fresh[consumed]
            consumed += 1
            if _fits(page, post, max_kind_run):
                page.append(post)
            else:
                waiting.append(post)
            continue
        if max_kind_run and take(0):
            continue
        if waiting:
            page.append(waiting.pop(0))
            continue
        break

    return AssembledPage(posts=page, consumed=consumed, carry=waiting)

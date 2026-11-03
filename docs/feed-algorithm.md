# Feed algorithm

## For You sessions

A request without a cursor opens a new session. The session fixes shuffle
parameters and the previously served set; subsequent pages carry that state in
an opaque cursor for up to six hours. The app must send no cursor on refresh and
replace its old list with the new first page. This service cannot control the
mobile refresh spinner or cache.

Eligible posts receive a final quality score based on engagement, freshness, and
report penalties. New and returning users see unseen posts before previously
served posts. Returning users blend discovery and followed creators 3:1 within
each seen tier; followed posts remain chronological within their tier. Posts
hidden by the user or over the report suppression threshold are excluded.

On the first page, the lead is chosen from at most `MAX_CARRY + 1` candidates:

1. Choose the best available seen tier (unseen whenever available).
2. Keep the top 20% by final quality score, at least two candidates where
   possible, and require at least 80% of the best score.
3. For a signed-in user, prefer candidates absent from their last 20 first
   posts (stored for 24 hours). Select within the band using the session's
   randomized offset. If no alternative passes the gate, the prior lead may
   repeat.

Any candidate passed over before the lead is consumed into the cursor carry,
not skipped. Later pages use the frozen ordering and keyset positions, preserving
no repeats or gaps. A replayed cursor gives the same page while its underlying
posts and permissions remain unchanged. An old session cursor from before the
unseen-first ordering starts a fresh session.

Guests have no stable identity. Each new guest session randomizes within the
quality band, but repeated slot 0 is possible, especially when only one post
passes the gate. Guaranteed guest rotation would require a persistent anonymous
identity. No ranking rule can guarantee a new post when the eligible catalogue
has not changed.

## Following and Discover

Following is chronological for followed creators. Its top post may remain the
same until a newer followed post appears. Discover is a separate ranked listing
with its own cursor. Neither uses the For You lead-history rule.

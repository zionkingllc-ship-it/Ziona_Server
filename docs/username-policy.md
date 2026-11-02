# Username and display-name contract

`username` is the @handle (ASCII letters, digits, and underscores). `fullName` is
the editable display name and may contain emoji. The resolved `displayName`
already trims `fullName` and falls back to `username` when no name is set.

## Editing a username

- Initial signup selection, including an OAuth account's one-time
  `finalizeUsername`, is not a change. `finalizeUsername` cannot be replayed.
- During the first 48 hours from `createdAt`, up to three *actual* changes via
  `updateUsername` are allowed. They do not start the 30-day cooldown. A no-op
  does not spend an edit.
- After that window, one change is allowed every 30 days. The first post-grace
  change starts the cooldown.
- A released handle is reserved for its previous owner for 14 days. Availability
  and suggestions hide it from other users; the previous owner can reclaim it
  while their edit policy permits. Handles are unique case-insensitively.
- Permanent deletion also reserves the released handle for 14 days, but the
  deleted account cannot reclaim it.

The authenticated `me` query exposes the grace deadline and remaining edits:

```graphql
query UsernameEditStatus {
  me {
    username
    createdAt
    lastUsernameChange
    usernameEditableUntil
    usernameGraceChangesRemaining
  }
}
```

`usernameEditableUntil` is an ISO-8601 timestamp when the account is inside
the grace period *and* has edits remaining; otherwise it is `null`. The mobile
app should compare it with its current time to show the countdown, and use
`usernameGraceChangesRemaining` to show the cap. After 48 hours, use
`lastUsernameChange` to determine the 30-day cooldown. A successful
`updateUsername` should be followed by refreshing `me`.

`updateUsername` returns `RATE_LIMIT_EXCEEDED` for either exhausted grace edits
or the post-grace cooldown, and `USERNAME_TAKEN` for another user's current or
reserved handle. Emoji entered in a handle are rejected by username validation;
emoji entered in `fullName` are allowed and shown through `displayName`.

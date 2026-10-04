# iOS Push Triage — prod (`com.zionking.ziona`)

Confirmed scope: App Store AND TestFlight builds affected (both use the
production APNs environment — sandbox/prod key-type mismatch is ruled out).
Affected users get in-app (bell icon) notifications but no push, so event
fan-out works and the break is strictly token-registration or send/delivery.

Mobile side already verified — do not re-check: the app registers the native
RNFB FCM token on permission grant and on every foreground
(`ziona-v1/providers/notificationProvider.tsx`), refresh is handled, the
backend accepts `fcm_like` tokens and sends to all active tokens regardless of
platform, and the APNs payload carries alert + sound + priority 10 with
`apns-push-type: alert` (`Ziona_Server/core/notifications/firebase.py`).
Prod Google sign-in works — do NOT touch OAuth config for this issue.

Firebase project for BOTH prod apps: number `787996855669`, id `ziona-app`
(`ziona-v1/google-services.json` + `GoogleService-Info.plist` agree).
The backend service account MUST belong to this project.

APNs Auth Key is confirmed present in Firebase. What remains is verifying it
is the RIGHT key/credential chain. Every step below is backend/consoles only.

---

## Step 1 — Token rows exist? (DB, 2 min)

```sql
SELECT token, platform, is_active, created_at
FROM device_tokens
WHERE user_id IN ('<affected-user-uuid-1>', '<affected-user-uuid-2>')
ORDER BY created_at DESC;
```

- [ ] No active `platform='ios'` row for users who demonstrably opened the
  app while logged in → the app never delivered a token. The APNs key being
  present is necessary but not sufficient: Firebase also needs the key
  assigned to THIS iOS app entry (`com.zionking.ziona`), and the app's
  provisioning profile needs the Push capability. Bounce to Step 3, then
  report back "no token minted" so mobile investigates `getToken()` errors.
- [ ] Active `ios` rows exist → the app did its job. Go to Step 2.

## Step 2 — `debugSendPush` per-token outcome (GraphQL admin, 5 min)

Run admin `debugSendPush(userId, title, body)` for an affected user. It sends
through the identical message builder as production with zero side effects
(`send_fcm_debug` in `core/notifications/firebase.py`). Act on `error_code`:

| `error_code` | Meaning | Fix |
|---|---|---|
| `success: true`, nothing displays | FCM accepted, lost at APNs | Step 3 (wrong APNs key material for this bundle) |
| `SENDER_ID_MISMATCH` / `mismatched-credential` | Service account project ≠ `787996855669` | Step 4 (replace Render secret) |
| `NOT_FOUND` / `registration-token-not-registered` | Token rotated or app reinstalled; row is stale | No fix needed — next app foreground re-registers via `onTokenRefresh`. If EVERY ios row looks like this, suspect Step 4 misconfiguration mass-invalidating sends |
| `INVALID_ARGUMENT` | Malformed token stored | Should be impossible (`register_device_token` rejects non-`fcm_like` kinds). If seen, pull the row — possible classifier gap, escalate with the token tail |
| `FIREBASE_NOT_INITIALIZED` | No credentials on this host | Step 4 (secret missing/unreadable) |

## Step 3 — APNs key is the RIGHT key (Firebase + Apple consoles, 5 min)

"Key present" was confirmed; verify assignment and scope:

- [ ] Firebase Console → project `ziona-app` → Project settings → Cloud
  Messaging → the **iOS app entry for `com.zionking.ziona`** (not just the
  project, not the Android app) has an **APNs Auth Key (.p8)** attached, with
  Key ID + Team ID (`RLL2NX9J5Z`). A key uploaded at project level but never
  attached to the iOS app entry sends nothing.
- [ ] Apple Developer → Identifiers → App ID `com.zionking.ziona` → Push
  Notifications capability **enabled** (required even with a .p8 key).
- [ ] If Firebase holds an APNs **certificate** instead of a .p8: it must be
  a **Production** certificate. Both affected builds are store-distributed
  (production APNs); a sandbox cert drops them silently. Preferred fix:
  replace with a .p8 key (covers both environments, never expires yearly).

## Step 4 — Service-account project (Render, 5 min)

- [ ] Render dashboard → prod service → Secret Files → open
  `firebase-credentials.json` → `"project_id"` MUST be the app Firebase
  project (`787996855669` / `ziona-app`). Any other value → generate a new
  private key from Firebase Console → `ziona-app` → Project settings →
  Service accounts, replace the secret file, redeploy.
- [ ] Render logs (prod service) — search, newest first:
  - `fcm_sender_id_mismatch` → confirms this step's mismatch (tokens are
    NOT deactivated by this path, so fixing creds heals without data loss).
  - `Firebase not initialized` / `Cannot send FCM message` → creds file
    missing or unreadable at `/etc/secrets/firebase-credentials.json`.
  - `push_notification_skipped_no_tokens` for affected users → back to Step 1.
  - `push_notification_dispatch_finished` with `failure_count > 0` →
    cross-reference with Step 2's per-token codes.
  - `device_token_rejected_unsupported_kind` → a non-FCM token reached
    registration (Expo/APNs-raw); pull `token_tail` from the log line.

## Step 5 — Close the loop

- [ ] Re-run `debugSendPush` for the affected user → expect `success: true`
  on the ios token.
- [ ] Trigger a real event (like their post from a second account) → push
  arrives with badge + sound on the App Store/TestFlight build.
- [ ] If Steps 1–4 all check out and pushes still don't arrive, escalate with
  this packet (nothing else is needed to find it): `debugSendPush` output,
  the `device_tokens` rows, the `project_id` from the Render secret, the
  APNs key type attached to the iOS app entry, and the Render log lines
  from Step 4.

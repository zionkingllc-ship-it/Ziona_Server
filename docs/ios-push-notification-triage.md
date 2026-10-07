# iOS Push Notifications — Backend Triage TODO

## Symptom
iOS devices receive no push notifications. Android delivery works.
Google Sign-In on prod is confirmed working — do not touch the OAuth setup.

## Already verified in code (no repo fix needed)
- Client registers the native RNFB FCM token (`providers/notificationProvider.tsx`),
  not an Expo token — backend accepts it (`core/notifications/services.py:921`).
- APNs payload is correct: alert + `sound: default` + `apns-priority: 10` +
  `apns-push-type: alert` (`core/notifications/firebase.py:90`).
- Credential mismatch is logged loudly, never silent
  (`fcm_sender_id_mismatch`, `firebase.py:170`).
- Both prod apps live in ONE Firebase project (`787996855669` / `ziona-app`),
  so a single service-account credential can serve both platforms.
- `GoogleService-Info.plist` (prod) vs `app.config.js` iOS client IDs differ
  (`787996855669-tkfj17…` vs `433767985127-af63…`) — this is EXPECTED
  (Firebase project vs OAuth project). Do NOT "align" the plist.

## TODO 1 — Confirm whether iOS tokens exist (2 min, DB shell)
Affected user has zero rows → client-side (permission / APNs key). Rows present
but nothing arrives → send-side (credential / APNs delivery).

```python
from core.notifications.models import DeviceToken
qs = DeviceToken.objects.filter(user_id="<USER_ID>", platform="ios", is_active=True)
print([(str(t.id), t.token[-8:], t.created_at) for t in qs])
```

## TODO 2 — Run the admin diagnostic (5 min, fastest single test)
GraphQL admin-only `debugSendPush` for the affected user returns per-token
`error_code`. Read it literally:

| `error_code` | Meaning | Fix |
|---|---|---|
| success | FCM accepted; problem is APNs delivery or app state | Go to TODO 4 |
| `SENDER_ID_MISMATCH` / `mismatched-credential` | Service account is from the wrong Firebase project | Go to TODO 3 |
| `NOT_FOUND` / `registration-token-not-registered` | Stale token (app reinstalled) | Have user reopen the app (re-registers); check TODO 1 again |
| `FIREBASE_NOT_INITIALIZED` | Creds missing/broken on this env | Go to TODO 3 |
| `INVALID_ARGUMENT` | Token was stored in a kind FCM can't deliver | Check registration path; should not happen for RNFB tokens |

## TODO 3 — Verify backend Firebase wiring (Render dashboard + logs)
1. Render → prod service → Logs: search `fcm_sender_id_mismatch`,
   `Firebase not initialized`, `Failed to initialize Firebase`.
   - `fcm_sender_id_mismatch` names the project the server is authed to
     (`server_project_id`). It MUST be `787996855669` / `ziona-app`.
2. Render → prod service → Secret Files: open `/etc/secrets/firebase-credentials.json`
   and confirm its `project_id` is the `ziona-app` Firebase project
   (number `787996855669`). If it belongs to any other project, generate a new
   service-account key from Firebase Console (`ziona-app` → Project settings →
   Service accounts) and replace the secret file. Redeploy after replacing.
3. While in the dashboard, confirm `GOOGLE_CLIENT_IDS` includes the iOS audience
   `433767985127-af63p5o4ahgk4voiqv4u7mj0a7fm3gfv.apps.googleusercontent.com`
   (prod Google Sign-In works today, so this is a guard, not a fix).

## TODO 4 — Verify Apple push config (Firebase Console, most likely cause)
Firebase Console → project `ziona-app` → Project settings → Cloud Messaging →
iOS app `com.zionking.ziona`:
1. Is an **APNs Authentication Key** uploaded? If missing, iOS cannot mint FCM
   tokens at all (`getToken()` fails on-device) and FCM cannot deliver.
   Create one in Apple Developer → Keys (Apple Push Notifications service),
   upload the `.p8` + Key ID + Team ID (`RLL2NX9J5Z`) to Firebase.
2. Confirm the key is the production one shared by the App Store app
   (sandbox-only keys break store builds and vice versa).
3. Ask the reporter which iOS build is affected (TestFlight/App Store vs
   dev-client/sideload) — sandbox/production APNs mismatch only bites
   non-store builds.

## Acceptance criteria
- [ ] Affected iOS user has an active `ios` row in `device_tokens`.
- [ ] `debugSendPush` for that user returns `success` for the iOS token.
- [ ] Test push arrives on the physical iOS device with sound + banner.
- [ ] Render logs show no `fcm_sender_id_mismatch` for 24h after the fix.

## Reference (repo pointers)
- Sender + APNs payload: `Ziona_Server/core/notifications/firebase.py:62`
- Registration + token classification: `Ziona_Server/core/notifications/services.py:810,921`
- Client registration: `ziona-v1/providers/notificationProvider.tsx:100`
- Client IDs: `ziona-v1/app.config.js:29` (prod iOS `433767985127-af63…`),
  `ziona-v1/GoogleService-Info.plist:5` (Firebase iOS client, leave as-is)

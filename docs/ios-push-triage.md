# iOS Push Triage — production (`com.zionking.ziona`)

Reported symptom: App Store and TestFlight users see in-app notifications but
not system push notifications. This proves that notification rows exist, **not**
that the Celery push task ran or that FCM/APNs delivered the message. Both release
channels use production APNs; their shared failure does not rule out a missing
or incorrectly scoped production APNs credential.

This is an operational checklist, not a claim that the deployed secrets or
installed builds have been verified. Do not change working Google sign-in/OAuth
configuration as part of push triage. Do not rotate keys or deactivate token
rows until the diagnostic evidence identifies the cause.

## Environment and build identity

The mobile `V1-0` snapshot reviewed was `680daf1ebc90bdf34a2f953d7bbcc2bdfd2da8b7`.
Its production/staging Firebase configuration deliberately differs:

| Environment | Bundle/package | Firebase project ID | Sender/project number | URL scheme |
|---|---|---|---|---|
| Production | `com.zionking.ziona` | `ziona-app` | `787996855669` | `ziona` |
| Staging | `com.zionking.ziona.staging` | `ziona-prod` | `273573551303` | `zionastaging` |

These are FCM projects, not Google OAuth client IDs. Record the actual installed
version, build number, source commit, EAS profile, and API hostname. The newer
`fix/staging-google-oauth` branch contains mobile registration/routing changes;
the branch name alone does not identify the shipped binary.

`V1-0` requests a native RNFB FCM token on permission grant/foreground and listens
for refresh, but skips registering an unchanged cached token. Its cache is not
scoped to the signed-in user. Foregrounding or switching accounts therefore
does **not** prove registration happened or repair an inactive/misowned row.
Collect the registration response and correlate the current device token's
masked tail with the correct account; do not conclude that mobile is ruled out.

## 1. Check registration without exposing tokens

Use a read-only query in the correct environment's PostgreSQL database:

```sql
SELECT id, user_id, RIGHT(token, 6) AS token_tail,
       platform, is_active, created_at, updated_at
FROM device_tokens
WHERE user_id = '<affected-user-uuid>'
ORDER BY created_at DESC;
```

- No matching active iOS row: investigate notification permission, RNFB/APNs
  registration errors, authenticated `registerDeviceToken` responses, account
  ownership, and whether mobile called the correct API. A missing row alone does
  not prove no token was minted.
- Matching active row: continue below. A row can still contain a stale token or
  one issued by a different Firebase project. `fcm_like` is a shape heuristic,
  not proof that the token is valid.
- Inactive row: determine why it was deactivated. Do not bulk-reactivate rows or
  assume an `onTokenRefresh` callback occurs on every foreground. Arrange a
  deliberate authenticated re-registration with mobile after fixing the cause.

## 2. Send one authorized diagnostic and inspect per-token results

Coordinate with the affected tester: **this sends a real notification to every
active device token on the target account**, including Android tokens. It does
not deactivate tokens, but is not side-effect-free. Avoid accounts with many
devices; the debug sender is a single multicast request (maximum 500 tokens).

POST to `https://api.ziona.app/graphql` using `Content-Type: application/json`
and `Authorization: Bearer <admin-access-token>`. The admin must be active and
the access token must carry the admin role. Keep the header/token private.
Use this operation with variables `{"targetUserId": "<affected-user-uuid>"}`:

```graphql
mutation DebugIosPush($targetUserId: ID!) {
  debugSendPush(targetUserId: $targetUserId, includeInactive: false) {
    success
    error { code message }
    projectId
    tokensTried
    successCount
    failureCount
    results {
      tokenPreview
      platform
      isActive
      tokenKind
      success
      messageId
      errorCode
      errorMessage
    }
  }
}
```

The mutation does not accept `userId`, `title`, or `body`. First check GraphQL
`errors` and the payload `error`. Top-level `success: true` only means the
diagnostic completed: it can coexist with zero successful sends. Require the
intended iOS result's `success: true` and a message ID; `tokensTried: 0` tests no
delivery. FCM acceptance still does not prove the device displayed a banner.

This executes inline on the API using the production message builder. It does
not test the Celery worker, normal-event preferences/fan-out, or notification
tap routing. The debug payload is not a real post/comment destination.

### Interpreting this backend's Python SDK errors

`firebase-admin==6.3.0` exposes generic Python codes for several specialized
exceptions. GraphQL returns `errorCode`/`errorMessage` (camelCase); REST or Node
SDK tables alone are insufficient. Inspect both fields before deciding a fix.

| Per-token outcome | Interpretation and next check |
|---|---|
| `success: true` with a message ID, no display | FCM accepted it. Check notification permission, Focus/Scheduled Summary, app foreground presentation, device connectivity, and the APNs/build checks below. Do not assume a bad key. |
| `PERMISSION_DENIED` | Includes Python `SenderIdMismatchError`, but can also indicate IAM/API permission problems. Check the message, effective target project, token origin, and service-account permissions. Other stacks may call this `SENDER_ID_MISMATCH` or `messaging/mismatched-credential`. |
| `UNAUTHENTICATED` | Includes Python `ThirdPartyAuthError`. If the message names APNs, inspect the APNs credential chain; otherwise inspect server authentication. Other stacks may report `THIRD_PARTY_AUTH_ERROR`. |
| `NOT_FOUND` | Includes Python `UnregisteredError`. Confirm its message before treating a token as unregistered; correlate with the device's current token and obtain a fresh registration if needed. |
| `INVALID_ARGUMENT` | Inspect both message payload and token. Invalid payload fields can cause this too; `fcm_like` classification does not rule out a malformed token. Do not prescribe token deletion based on this code alone. |
| `FIREBASE_NOT_INITIALIZED` / `SDK_NOT_INSTALLED` | Check the deployed SDK, credential path, file readability, and initialization logs. |
| `SEND_FAILED` / `UNKNOWN` / other codes | Preserve the redacted error message; check transport, quota, transient service failures, and initialization rather than guessing. |

The existing production sender deactivates tokens for `NOT_FOUND` and
`INVALID_ARGUMENT`; the debug sender does not. If many rows became inactive,
investigate historical payload/errors and registration before retrying a real
event. This runbook does not change that runtime behavior.

## 3. Verify both API and worker on Render

An operator with access must check **`ziona-api-prod` and `ziona-worker-prod`**:

- Confirm the deployed commit and that both use the expected production DB and
  broker; the worker must consume the `default` queue used by push tasks.
- Confirm `FIREBASE_CREDENTIALS_FILE` points to the mounted, readable
  `/etc/secrets/firebase-credentials.json` on each service. A path in `render.yaml`
  does not create/provision the secret file. Never paste the JSON/private key
  into a ticket, PR, log, or chat.
- Inspect only credential metadata privately. Check `FIREBASE_PROJECT_ID` as
  well as the credential's project: this setting can override the target.
  The production **effective Firebase app project** must be `ziona-app`.
  The diagnostic's `projectId` reports the API's effective project, not the
  worker's. Verify the worker independently. In a service shell, the following
  prints only that service's effective project (not its credentials):

  ```sh
  python manage.py shell -c 'from core.notifications.firebase import get_fcm_project_id; print(get_fcm_project_id())'
  ```

- A service account from `ziona-app` is the normal setup. A different credential
  project can be valid with explicit cross-project FCM IAM permissions and the
  correct target project; do not rotate a working key solely for that difference.
- If evidence requires an approved configuration change, redeploy/restart both
  services so running processes reload Firebase initialization. Change only the
  identified setting/credential; leave OAuth settings alone.
- Correlate UTC timestamps and the affected account across API and worker logs:
  `debug_push_sent`, `push_notification_dispatch_started`,
  `push_notification_dispatch_finished`, `push_notification_task_failed`,
  `push_notification_skipped_no_tokens`, `fcm_invalid_tokens_deactivated`, and
  `device_token_rejected_unsupported_kind`. Also inspect initialization failures
  and `Cannot send FCM message` messages. A finished task/dispatch log alone is
  not delivery proof; check counts and device receipt.
- Do not require `fcm_sender_id_mismatch` as evidence: the current production
  code checks named REST/Node codes, so Python's `PERMISSION_DENIED` may not
  produce that log. Its absence does not rule out a sender mismatch.

## 4. Verify the production APNs chain and device

In Firebase project `ziona-app`, inspect Cloud Messaging settings for the iOS
app `com.zionking.ziona`. Confirm the configured credential, Key ID and Apple
Team ID `RLL2NX9J5Z`, and that the key has not been revoked. In Apple Developer,
confirm Push Notifications capability and the **signed installed build's**
`aps-environment` entitlement/provisioning profile, not only source configuration.

Check the credential's actual production/environment and topic scope. Newer
APNs `.p8` keys can be environment-specific or topic-specific; not every `.p8`
automatically covers both environments/all apps. If using a certificate, verify
production support, expiry, and bundle/topic. "A key is present" is insufficient.
Do not revoke or replace credentials without identifying the mismatch and its
impact on other builds/apps.

On the device, verify notification permission, alert/sound settings, Focus,
Scheduled Summary, connectivity, and foreground presentation behavior. Test
background/locked and foreground states separately. The backend sends an alert
and sound but **no `aps.badge`**; the inspected mobile code synchronizes the badge
from unread counts. A missing badge alone is not evidence of failed push.

## 5. Close the loop and preserve a safe escalation packet

- Re-run the authorized diagnostic and confirm the intended iOS per-token
  result and visible receipt on the recorded App Store/TestFlight build.
- Trigger a real supported event from a second test account, with preferences
  enabled. A like may be batched; allow the configured batching window. Verify
  API enqueue, worker processing, counts, device receipt, and tap destination.
- Repeat with the app foregrounded, backgrounded/locked, and normally closed.
  Record installed build/OS and permission state for each result.
- Escalate if needed with build identifiers, UTC timestamps, affected account
  IDs through a restricted channel, token tails, redacted diagnostic outcomes
  and logs, both effective project IDs, and non-secret APNs scope metadata.
  Review `errorMessage` and `tokenPreview` before sharing: short malformed tokens
  may be returned without masking. Never include full tokens, bearer credentials,
  `.p8` material, or service-account JSON. This packet guides further diagnosis;
  it does not guarantee the cause is exclusively on the backend.

## Separate deep-link release checks

Push delivery and opening shared posts are separate checks. Keep `/post/{id}`
and `ziona://viewer/{id}` (staging: `zionastaging://viewer/{id}`) unchanged.
Verified App/Universal Links can open directly. If the preview appears, opening
the app requires tapping its button; there is no timed automatic store redirect.
Test Chrome/Safari/in-app browsers, installed/uninstalled states, repeated taps,
and cold/warm launches. Desktop previews must remain readable. Profile sharing
is not upgraded to a custom-scheme fallback by this change.

The reviewed mobile association file lists a staging EAS certificate beginning
`5B:5B` and ending `C8:8B` that was absent from both live staging hosts on
2026-10-05. This PR does not authorize a new certificate. Confirm the actual
APK signing SHA-256 with the mobile release owner, then append the verified
fingerprint to staging's `ANDROID_SHA256_CERT_FINGERPRINTS` and deliberately
update its deployment test. Preserve existing authorized entries until reviewed.
Recheck `/.well-known/assetlinks.json` on both `staging.ziona.app` and
`api.staging.ziona.app` and reverify links on the affected device.

## References

- [Reviewed mobile configuration and source](https://github.com/zionkingllc-ship-it/Ziona-v1/tree/680daf1ebc90bdf34a2f953d7bbcc2bdfd2da8b7)
- [Firebase cross-project service-account authorization](https://firebase.google.com/docs/cloud-messaging/send/v1-api#authorize_a_service_account_from_a_different_project)
- [Apple APNs key environment and topic scope](https://developer.apple.com/help/account/keys/create-a-private-key/)
- [Chrome intent launch and user-gesture requirements](https://developer.chrome.com/docs/android/intents)
- [Android website association and signing certificates](https://developer.android.com/training/app-links/configure-assetlinks)

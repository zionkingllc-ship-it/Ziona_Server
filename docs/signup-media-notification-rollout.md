# Signup, media and notification diagnostics rollout

## Behavior

`OTP_EMAIL_QUEUE_FAILED` returns HTTP 503 with the existing `{success:false,error:{code,message,details}}` envelope. An account created before queue failure stays unverified. Recover with `/api/auth/resend-otp` after the broker/worker recovers; do not repeat registration to recover this failure.

Audit events distinguish `auth.register.created`, `auth.register.verification_unavailable` and `auth.register.success`. Success means verification email enqueue succeeded, not that an email reached the inbox.

MOV/QuickTime and MP4 header validation reads `ftyp` after the four-byte box size. This does not bypass upload size/type validation or processing/video-stream checks. Mobile must encode HEIC/HEIF as JPEG rather than rename the original bytes; its upload descriptor carries final URI, MIME, filename and measured size together.

Unauthenticated notification GraphQL queries retain authentication protection and return `extensions.code=UNAUTHENTICATED`. Mobile waits for authentication before polling and clears auth query caches on logout.

## Staging gate

1. Deploy the verified backend commit to the staging API and worker with a rebuild that installs requirements. Verify installed versions in both shells:
   ```sh
   python -c "from importlib.metadata import version; print({p: version(p) for p in ('celery','kombu','redis')})"
   ```
   Expected pins: celery 5.3.4, kombu 5.6.0, redis 5.0.1. Keep each service's existing environment and credentials.
2. Register an owned test email through the new mobile build. Confirm account creation, a worker email task and actual email arrival. A Redis ping or task submission alone is not acceptance.
3. Exercise enqueue failure/recovery in an isolated staging test, without interrupting shared staging traffic. Verify HTTP 503/code, the same unverified account ID, then successful resend and OTP verification. Automated regression tests cover the failure without disabling live services.
4. On devices, upload native iPhone HEIC, JPEG and MOV and Android media through posts, draft resume, circles and avatar. Confirm successful processing/playback. Malformed and mislabeled files must remain rejected.
5. Open as a guest, then log in/out. Confirm notification counts are queried only after authentication and don't survive logout. Trigger a comment from a second account: verify the worker dispatch counts and actual alert.

## Production gate

Only promote after staging CI and device acceptance pass. Rebuild both production API and worker; repeat version verification, queued signup/resend, uploads and real-event push checks with owned test accounts. Record deployed SHA, environment, test time and outcome. Do not include tokens, OTPs, passwords or credential contents in evidence.

The missing iPhone like alert remains a separate presentation/delivery diagnostic: reproduce a fresh like, correlate its event with the target device result, app state and notification settings. FCM acceptance is not proof that iOS displayed the notification.

No schema migration is introduced by this repair. Rolling application code back does not remove accounts already created. A backend rollback can reintroduce MOV rejection/incorrect failure status, so retain resend guidance and diagnose before reverting.

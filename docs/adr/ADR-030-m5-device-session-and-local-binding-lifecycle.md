# ADR-030: M5 device session and local binding lifecycle

- Status: Accepted by the user on 2026-08-20
- Date: 2026-08-20
- Scope: Product M5A contract only; implementation deferred to M5B

## Context

P03 correctly stores refresh hashes and fails closed on rotated-token replay, but a newly enrolled
device also needs proof of possession and exact lost-response recovery without storing recoverable
bearers server-side. Android binding spans non-secret settings, Keystore secrets and optional local
intent materialization.

## Decision

1. M5 enrollment creates a non-exportable P-256 Android device key. The logical `device_id` is bound
   to that public key; reinstall/key loss is a new device.
2. Invitation exchange is atomic and idempotent only for the same exchange ID, canonical request
   hash and device-key proof. It creates exactly one device/session.
3. Additive rotation v2 uses a client-generated next refresh secret. The server receives only its
   SHA-256 hash and atomically creates one successor session with parent/family/generation lineage.
   Exact signed replay may recover the same successor; changed replay retains P03 device revocation.
4. Legacy P03 sessions and endpoints are not silently changed.
5. Local disconnect, current logout, all-session logout and device revoke are separate commands.
   Offline/timeout is `remote_outcome_unknown`; only local disconnect may clear local credentials
   without claiming a remote result. Library, media and outbox remain.
6. Every async result is guarded by an immutable flow generation and full trust/account/device
   snapshot. DataStore and Keystore use a matching binding commit marker for crash recovery;
   WorkManager/Room do not carry secrets.
7. Existing local intent is materialized only after explicit bounded review and last-moment binding
   revalidation. ADR-018 transactions create new immutable events; no prior identity/hash is rewritten.
8. Exchange/rotation receipts keep only hash/public evidence through absolute session expiry plus
   five minutes; bounded cleanup completes within 24 hours. Secret endpoint responses, including
   errors, require `Cache-Control: no-store` and `Pragma: no-cache`.
9. Exact receipt replay reloads and locks the bound account/device/successor session before minting
   access. Account disable/delete, device revoke, session logout/expiry and logout-all fail closed;
   a receipt is idempotency evidence, not authorization.

## Consequences

M5B needs additive PostgreSQL invitation/exchange/rotation receipt and session-lineage persistence, plus
versioned Android DataStore/Keystore changes. Room v12, P04 event schemas and P09 cursor semantics do
not change. Legacy devices remain secure but do not gain rotation-v2 exact-response recovery until
re-enrolled.

## Rejected alternatives

- Store encrypted server-issued replacement refresh credentials for replay: creates a recoverable
  bearer on the server.
- Modify `/auth/refresh` semantics in place: breaks frozen P03 replay behavior.
- Use `server_profile_id` as an authorization principal: violates P04/P05 boundaries.
- Automatically attach all standalone intent: violates F-018 and ADR-018.
- Put tokens/origins/enrollment payloads in Room, ordinary DataStore or WorkManager input.

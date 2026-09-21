# Account recovery server slice: 2026-09-18

The later [Android recovery verification record](ADMIN_ANDROID_RECOVERY_VERIFICATION_2026_09_18.md)
supersedes the client next-work status below. This record retains the server evidence.

The server block is implemented and verified locally. The complete TXT/manual recovery
feature remains unfinished: Android screens, import/export, explicit account/server
confirmation and durable encrypted pending-operation integration are the next work.
The feature flag remains disabled by default; there was no deployment or production migration.

## Implemented behavior

- Hash-only 160-bit recovery codes, scoped to server/account, strict bounded versioned TXT
  parsing and manual normalization. Codes and private origins are excluded from object repr.
- Active V2 application sessions of all account roles configure/rotate codes using compare
  and set. Existing accounts are not silently given server-generated secrets.
- A fresh P-256 device proves its key and code before receiving the account preview; commit
  explicitly binds the confirmed account, server identity/origins, code generation, new
  code verifier, client-created refresh hash and binding commit ID.
- One transaction increments account authority generation, revokes old application/browser
  sessions, passkeys and trusted keys, cancels unfinished pairing/admission and invitations,
  withdraws resource grants and replaces the device/session. The account stays ACTIVE and
  durable data remain. Retained process executions do not release charged capacity.
- Immutable one-day exact-operation receipts recover a lost response for the same still-live
  binding. Superseded generations, revoked result credentials and changed operation reuse
  are denied. No plaintext refresh token is generated or persisted by this flow.
- Bounded HTTP, private code header, no-store success/error responses, persistent source,
  global and account attempt limits, optional capability advertisement and CPU-worker
  expired-receipt cleanup are wired. Persistent profile identity is required for enablement.

Contract: [account recovery v1](../design/AutPlay_Account_Recovery_Contract_v1.md).
Wire schemas: `contracts/openapi/v1/autplay-account-recovery.openapi.json`.

## Persistence and verification

Current head: `0051_account_recovery`, following `0050_metadata_execution`.
Inventory: **159 tables, 1815 columns, 152 explicit indexes**. Mapping fingerprint:
`0a56ebeb802b25b8e45f4aba40bd7d96e7a9d2846e674816e168a102a9e1bd72`.
Recovery credentials require monotonic rotation; receipts cannot be rewritten or deleted
before expiry. Downgrade acquires table locks before checking for durable evidence.

The final affected batch passed **65 tests in 58.40 seconds**, with no skips, using locked
Python dependencies on Windows and the disposable PostgreSQL 18.4/pgvector container at
loopback port 1520. Coverage includes full revocation, exact retry, two consumers racing
for one code, rollback after revocation but before replacement, invalid/disabled/deleted
authority, expired-receipt cleanup, immutable SQL state, an in-flight insert versus downgrade,
HTTP authentication replacement and bounds, TXT validation, self-device pairing regression,
metadata fingerprint, live Alembic drift, PUBLIC ACLs and linear migration history.

The initial targeted run passed 33 cases and exposed a test fixture missing mandatory settings.
That fixture was corrected; the final 65-case batch includes those cases and is the acceptance
count, not an additional 65 independent cases. Ruff and formatting passed on 20 changed Python
files; strict mypy passed on 16 selected implementation/test files. Locked root tooling
validated the OpenAPI document and its JSON Schema definitions. `git diff --check` passed.

Bounded independent read-only review found two issues: concurrent downgrade could discard
new evidence, and a NUL in app version could reach PostgreSQL. Both were fixed, regression
coverage was added, and the reviewer confirmed closure without running extra test batches.

## Subsequent Android closure and remaining acceptance

The Android client work listed here as open was completed later on 2026-09-18 and is recorded in
[Android account recovery verification](ADMIN_ANDROID_RECOVERY_VERIFICATION_2026_09_18.md).
The current client creates and stores the next code and refresh secret before sending, preserves
exact requests across interruption, verifies the selected server before disclosing a TXT/manual
code, requires explicit account confirmation, promotes the returned binding atomically, and
exports the replacement recovery document. Shared proof fixtures cover Android/server
interoperability. New PA2 accounts retain an incomplete-setup checkpoint until the exact recovery
document is written, read back and acknowledged; local music remains available meanwhile.

The later deletion, restore-fence, consent and worker/byte-path blocks are also implemented and
indexed by [the unified goal status](ADMIN_UNIFIED_GOAL_2026_09_19.md). Physical A55/system-picker,
private-network/real-WebAuthn, isolated-backup and target joint-measurement acceptance remains
open. This local server block alone does not complete the goal.

# ADR-053: Admin Web and account self-service extensions

- Status: Implemented locally; target deployment and physical acceptance in progress
- Date: 2026-09-16
- Authorization: user requested implementation of the accepted decisions in
  `docs/design/explorations/AutPlay_Admin_And_Account_Onboarding_Draft_v1.md`.

## Decision and compatibility

Implement the following additive boundaries. Existing M5/M6/PA2 authority continues to apply
until an explicitly enabled new protocol takes over its named operation. This record authorizes
local implementation; it is not production rollout or acceptance evidence.

1. Group Admin Web into Overview, Accounts and devices, Music and transfers, and Server.
   Reuse owner-scoped application queries and command confirmation paths. Navigation never
   grants cross-account access. Show resource editors only alongside working enforcement.
2. Extend ADR-031/032 with WebAuthn browser registration and login. Keep existing opaque M6
   sessions, exact Origin, synchronizer CSRF, operation IDs, rotation, and local CLI bootstrap.
   Registration requires current administrative browser authority; login requires a fresh,
   one-time, preauth-bound challenge, verified RP/origin/challenge/signature/UP/UV and exact
   credential/account user-handle binding. Require discoverable credentials and attestation
   `none`. Reject embedded/cross-origin ceremonies. Persist public credentials, never private
   keys or replayable browser cookie values. Bounded bundled JS uses existing self-only CSP.
3. Passkey registration/revocation is separate from device pairing and account recovery.
   A synchronized passkey does not prove physical-device admission. Private network policy
   must independently admit only the configured laptop and A55. Canonical HTTPS origin/RP
   and network admission must be verified before real credential registration/rollout.
4. Extend ADR-046 through a separate self-service ceremony for all ACTIVE account roles.
   Bind the ceremony to the initiating account/device/session, exact new key, comparison code,
   expiry and operation. Require approval by the existing phone and explicit confirmation of
   the account by the new phone. Preserve existing bindings and recovery code. Do not relax
   existing OWNER/ADMIN checks or introduce an enumerable cross-account request queue.
5. Add account recovery with a bounded versioned TXT/manual code, random 32-character code,
   server/account identity confirmation and proof by the new device key. Atomically consume
   the old code generation, revoke all prior application/browser sessions, trust, passkeys,
   invitations and pending admission authority, then create the new binding. A disabled or
   finally deleted account cannot be restored by this code. Persist encrypted client pending
   operation/new credentials before sending; exact receipt replay cannot create another branch.
6. Add reversible deletion-pending lifecycle, distinct from disable and final purge. Suspend
   access immediately; allow only explicit ownership-proved cancellation before the 30-day
   deadline. Preserve durable data until purge. In particular, migrate the existing social
   cleanup trigger so suspension does not delete friendships/settings prematurely. Final
   purge includes independently retained deletion evidence, reapplied before serving a restored
   backup. Existing sync entity tombstones are not sufficient backup deletion evidence.
   User decision, 2026-09-18: reject deletion of the last ACTIVE, nondeleted OWNER.
   Another account must already hold effective OWNER authority before an OWNER may
   enter deletion pending. Check this inside the serialized lifecycle transaction;
   concurrent requests must not remove the final owner. Pending, disabled or deleted
   OWNER rows do not count. Do not auto-promote another account or reopen bootstrap.
   Ownership transfer is a separate operation, not an implicit side effect of deletion.
7. Shared-model training is opt-in at account level with monotonic consent revision. Unknown,
   denied, withdrawn and deletion-pending accounts cannot contribute to new shared training.
   Validate consent before source preparation, job start and publication; invalidate and remove
   affected dataset examples and pending jobs on withdrawal. Personal recommendations remain.
   Completed models continue serving until normal replacement. This training-withdrawal policy
   is distinct from the existing signed privacy-deletion policy
   `REVOKE_DATASET_AND_DESCENDANT_ARTIFACTS_V1`; do not reinterpret old signed artifacts.
8. Default account quotas are 5 active application devices, 2 logical server playbacks and
   2 background file transfers combined. Support bounded positive global defaults and per-account
   overrides with revision compare-and-set, operation receipts and sanitized audit. Only bootstrap
   OWNER may edit server/self/provisioned-account quotas. PostgreSQL serializes admission across
   processes. Range/seek requests share a playback lease. Lowering a limit preserves admitted
   devices/work; subsequent admission observes new limits. Device/passkey/browser counts differ.
   Background work uses bounded expiry, fairness and playback priority; global capacity needs
   measured evidence, not an invented production throughput guarantee. The user-approved
   2026-09-16 [I/O stop amendment](../design/explorations/AutPlay_Resource_IO_Stop_Amendment_v1.md)
   expires application I/O authority within five seconds but retains capacity for each durable
   process execution until confirmed exit; crash orphans require verified reconciliation.

## Verification and implementation constraints

### Browser passkey protocol

`AUTPLAY_ADMIN_PASSKEYS_ENABLED` defaults to false and requires enabled Admin Web with its
canonical HTTPS origin. `/admin/passkeys/options` and `/verify` require the current M6 actor,
CSRF token and operation UUID. Registration options replay only for the same actor/session/
generation; the challenge expires after five minutes. At most three registration ceremonies
may be pending and eight credentials active per account. Login options require the preauth
cookie and nonce from the login page; login verification also supplies ceremony and operation
IDs. Both ceremonies require verified discoverable WebAuthn user presence and verification.
Payloads are bounded at 16 KiB. Rate gates run before signature verification.

Registration completion atomically stores public credential evidence and its receipt. Exact
completion replay returns the existing credential ID. Login completion atomically updates the
counter, consumes the challenge, issues one existing M6 session and audits the outcome. A lost
login response returns `browser_login_outcome_unknown` on exact replay and requires a new login;
the server never retains or reissues the original bearer. Changed replay returns a bounded error.
Ceremony operation IDs are a separate namespace from M6 lifecycle mutations; each is bound to
its purpose. Browser passkey revocations share the M6 terminal operation namespace, so reuse
for logout/revoke-session fails with `operation_conflict` in either direction.

Revocation closes all sessions issued from that credential and retains a bounded-lifetime
terminal receipt for an exact retry with the now-revoked cookie. Active credentials precede
revoked history in the bounded list. Local `web-passkey-list`, `web-passkey-revoke` and
`web-passkey-cleanup --limit N` provide operator recovery/maintenance without credential secrets
in output. Cleanup removes ceremonies expired for more than one day, in bounded batches.
The migration refuses downgrade while passkey/ceremony/revocation evidence exists.

Bundled Admin form transport uses same-origin credentialed CORS-mode fetch to preserve exact
Origin under `Referrer-Policy: no-referrer`, including multipart imports and server-rendered
POST results. It retains operation IDs after uncertain transport outcomes; it does not retry
mutations automatically. Session material remains HttpOnly and is never browser JS storage.

### Acceptance gates

- Record protocol-specific schemas, atomic transitions, replay outcomes and bounded errors before
  enabling each feature. Maintain one compatible migration chain after ongoing Vault changes.
- Test real PostgreSQL races and rollback, wrong account/key/origin, revoked/expired authority,
  response loss, cleanup, and independent server processes. Preserve current M5/M6/PA2 tests.
- Test Android encrypted pending-state recovery, bounded polling, document parsing, local playback
  independence, consent refusal and quota messages. Use a single Gradle worker.
- Verify Web UI in EN/RU, keyboard, narrow/mobile and desktop modes. Virtual authenticators prove
  WebAuthn protocol behavior; physical Windows Hello/A55 and network exclusion remain separate
  acceptance evidence.
- Do not migrate production, modify live network admission, publish or deploy as part of local
  implementation without the user's separate authorization for the prepared result.

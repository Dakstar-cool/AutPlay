# AutPlay Public Invite-only Account Contract v1

| Field | Value |
| --- | --- |
| Status | ACCEPTED / IMPLEMENTED_PA2 |
| Version | 1.0 |
| Date | 2026-08-31 |
| Scope | Owner-issued creation of separate friend accounts and their first Android device |
| Implementation | Implemented and locally qualified by PA2; PA3 local edge candidate qualified, live deployment blocked |

## 1. Scope and standing invariants

This contract adds an invitation authority for creating a new server-scoped account. It extends,
but does not replace, M5 server discovery/application identity, M5 device/session rotation, S1B
exact-key trust, S1C same-server friendship or M6 browser security.

Android stays local-first. Local library and playback do not require this flow or a synchronous
server trip. PostgreSQL remains the account/session authority. Friendship, account creation,
device enrollment, session authority, Vault authorization and Room membership remain distinct.

The following are deliberately absent: open signup, usernames as global identity, email, password,
OAuth, password reset, account switching, public user search and public Admin Web.

## 2. Actors and authority

| Actor | Allowed | Explicitly not allowed |
| --- | --- | --- |
| Bootstrap OWNER | Create/list/cancel account invitations; list and disable exact accounts created by those invitations | Read or mutate another account's library, Vault, devices, sessions, profile, friends or Rooms |
| ADMIN | None of the PA1 provisioning operations | Role-label inference into server-wide account authority |
| Existing USER | Redeem no invitation while authenticated; use normal self-account operations | Create/manage accounts or choose a role |
| Unauthenticated Android | Redeem one valid invitation with exact device proof | List accounts/invitations, choose role, retry changed bytes or gain authority from discovery alone |
| Local operator | Existing bootstrap/recovery only | Silent account creation, bearer recovery or bypass of invitation evidence |

The bootstrap OWNER is identified by the existing unique active `OWNER` row and reloaded in every
transaction. PA1 grants only provisioning/lifecycle authority. It is not a general cross-account
administrator. An invited account is manageable only when immutable provisioning evidence links it
to an invitation issued by that exact OWNER on the same server instance.

Every OWNER mutation stores an idempotency receipt keyed by actor and operation UUID. The server
computes an RFC 8785/SHA-256 hash over the normalized command and binds the receipt to the actor,
action and exact path target. Only an exact match may replay the prior result. Reuse with changed
body, action or target returns stable `operation_conflict` and performs no mutation.

## 3. Account invitation

### 3.1. Creation

The OWNER supplies an operation UUID, an account display name and TTL. The server normalizes only
leading/trailing Unicode whitespace, rejects control/bidi-override characters, and requires 1-120
Unicode scalar values after normalization. The display name is fixed in the invitation and becomes
the initial account display name; redemption cannot replace it.

The server creates 32 random bytes and returns their unpadded base64url representation exactly
once. PostgreSQL stores only SHA-256 of the bearer. The response document contains:

- contract/schema version and invitation UUID;
- server instance UUID, identity epoch and SHA-256 identity thumbprint;
- normalized HTTPS API and stream origins;
- immutable display name and fixed role `USER`;
- issued/expiry timestamps and the bearer;
- the fixed secret-handling marker.

Default TTL is 600 seconds, minimum 60 and maximum 1,800. At most five active account invitations
exist per server and ten may be issued by the OWNER per rolling hour. Exact operation replay returns
the original non-secret invitation view but never re-emits the bearer; a lost secret-bearing create
response therefore requires cancellation and a new operation.

The server refuses issuance when 20 active non-deleted accounts already exist. Pending invitations
do not reserve an account slot; redemption locks the server account-cap row and rechecks the cap.

### 3.2. Delivery

The secret document is either an in-app QR or a non-HTTP `.autplayinvite` document with media type
`application/vnd.autplay.account-invitation+json`. It must never be placed in an HTTP path, query,
fragment, browser page, clipboard, notification body, analytics, logs, diagnostics or export.

Android secret-presenting/consuming screens set the secure-window/recents protections already used
by M5/S1. OS sharing may use a short-lived app-private content URI with read permission only for the
explicit recipient; the app deletes the staged file after expiry or confirmed handoff. The chosen
human delivery channel remains a user risk and is not server identity.

## 4. Trust and redemption

### 4.1. Required confirmation

Before sending the bearer, Android independently loads M5 discovery over platform-trusted HTTPS,
verifies the signed server application identity, and compares all invitation bindings. It displays
the server label, exact API/stream origins, owner-provided application fingerprint, account display
name, expiry and resulting role `USER`. A mismatch or HTTPS downgrade is terminal.

The current public release path does not rely on a user-installed private CA. Certificate renewal
is allowed when platform validation succeeds and the application identity is unchanged.

Redemption is an unauthenticated bootstrap operation. The server rejects any request carrying a
valid Authorization credential or recognized AutPlay session credential with safe `unauthorized`;
it never ignores or derives account context from that credential. Android must first be outside an
active profile, but this server-side rejection remains mandatory.

### 4.2. Signed request

Android creates a non-exportable P-256 key and a 256-bit successor refresh secret. The request
includes `registration_id`, `binding_commit_id`, invitation ID/bearer, every expected server/origin/
account-display binding, device description, SPKI/key thumbprint, successor refresh SHA-256 and
client nonce.

`request_sha256` is SHA-256 over RFC 8785 canonical bytes after removing `request_sha256` and
`device_signature_b64url`. The device signs the ASCII domain separator
`AutPlay account registration v1\n` followed by the raw 32-byte request hash using ECDSA
P-256/SHA-256 in fixed 64-byte IEEE P1363 form.

### 4.3. Atomic outcome and replay

One PostgreSQL transaction locks invitation, account-cap and relevant receipt keys, then rechecks:

- invitation hash/state/expiry and exact server/origin/display-name bindings;
- current OWNER/instance/account cap;
- canonical request hash, key thumbprint and proof;
- no existing changed receipt for the registration ID;
- every mutable account/device/session invariant used by M5 issuance.

First success atomically consumes the invitation and creates exactly one active `USER` account,
first logical Android device, generation-zero M5-compatible session, immutable provisioning link,
terminal registration receipt and sanitized audit event. No bearer is stored recoverably.

An exact lost-response replay requires the same registration ID, invitation-bearer hash, canonical
request hash and device-key thumbprint. The client submits a fresh valid proof over that same hash;
the ES256-P1363 signature bytes may differ because ECDSA signing is not required to be
deterministic. While the receipt remains valid, replay may mint a fresh access token for the same
session; the client-held refresh secret and its stored hash do not change. Any changed canonical
request, key or bearer returns `account_invitation_unavailable` and creates nothing.

The receipt remains through the session-family absolute refresh expiry, at most 90 days, plus five
minutes. Before replaying, the server reloads account, device and session. Disabled/deleted account,
revoked device or revoked/expired session returns the existing safe terminal error without a token.

## 5. Lifecycle after registration

The new account uses existing session rotation/logout/device revocation and S1C social behavior.
The registration bearer can never add another device or recover a later session. Under the
corrected proposed ADR-046 boundary, PA2 creates no trusted-key evidence: the invited `USER` cannot
use S1B reenrollment, issue an M5 additional-device invitation or transfer trust to any key.
Recovery/device addition is explicitly deferred under the correction accepted on 2026-09-01.

The OWNER may list only `user_id`, fixed provisioning invitation ID, display name, role, status and
creation/disable timestamps for invited accounts. The OWNER may disable an active invited `USER`
with an exact operation UUID and reason code. Create, cancel and disable all use the actor/action/
target/canonical-command receipt rule from section 2; changed operation reuse returns
`operation_conflict`. Disable is idempotent and immediately fails closed via existing
account/session authorization and S1C retirement. PA1 adds no re-enable or physical delete.

The OWNER cannot inspect or revoke individual friend devices/sessions through PA1 and cannot read
friend library, listening history, Vault, recommendations, presence or Room state unless a separate
existing user-mediated authorization already permits a bounded view.

## 6. Limits and abuse controls

| Boundary | Limit |
| --- | --- |
| Active accounts | 20 per server, including OWNER/ADMIN/USER |
| Active account invitations | 5 per server |
| Issue attempts | 10 per OWNER per rolling hour |
| Redemption attempts | 5 per invitation / 15 min; 10 per source token / 15 min; 30 per server / 15 min |
| Request body | 16 KiB |
| Display name | 1-120 Unicode scalar values after normalization |
| Device name | 1-120 characters |

The trusted TLS edge supplies a canonical source address only from an exact configured proxy. The
application immediately HMACs it with a dedicated secret and stores only the bounded token in rate
windows. Raw IP is neither identity nor persistent audit data. When proxy trust is unavailable or
ambiguous, the request consumes the stricter server-global budget rather than trusting a header.

Rate-limit, absent, expired, cancelled, consumed and changed-replay responses are non-disclosing.
No response confirms whether a display name, account or invitation ID exists.

## 7. Stable results and errors

Successful creation returns `201`; exact non-secret operation replay returns `200` without bearer.
First redemption returns `201`; exact lost-response replay returns `200` with `replayed=true`.
Cancellation, disable and their exact duplicates return a typed terminal result.

Stable errors are `account_invitation_unavailable`, `account_invitation_secret_lost`,
`account_registration_rate_limited`, `account_capacity_reached`, `operation_conflict`,
`transport_trust_required`,
`server_identity_changed`, `stale_flow_generation`, `auth_attention_required`, `device_revoked`,
`session_revoked`, `remote_outcome_unknown` and `unauthorized`. Safe messages contain no raw bearer,
source address, private origin, display name or account existence evidence.

## 8. Persistence and API impact

PA2 may add, through an additive Alembic migration:

- `account.registration_invitation` with hash-only bearer and terminal state;
- `account.registration_receipt` with request/key/session evidence and bounded expiry;
- `account.provisioning_link` from invited account to issuer/invitation/server identity;
- bounded issuer/invitation/source/server rate windows;
- operation receipts and sanitized audit evidence.

No existing account/device/session/profile-pairing table is destructively changed. No Room migration
is required: Android keeps pre-commit secret/checkpoint material in process/Keystore-backed state
and commits the resulting ordinary M5 profile binding through the accepted crash-order rules.

The draft OpenAPI defines six operations: create/list/cancel account invitations, redeem one,
list invited accounts and disable an invited account. It does not add `/register`, `/login`,
password or email routes.

## 9. Public deployment prerequisites

PA1 does not claim a production topology. PA3 must prove all of the following before WAN enablement:

- public platform-trusted TLS for exact API and stream origins;
- only TCP 443 reachable; mobile API and stream are behind the trusted edge;
- Admin Web, PostgreSQL, worker, internal health and raw service ports remain non-public;
- exact trusted-proxy parsing and spoofed-forwarded-header rejection;
- encrypted off-host PostgreSQL/Vault/secret backup and isolated restore;
- stable Android signing custody and an upgrade-compatible distribution path;
- real mobile-network Range streaming and renewal/rollback evidence.

WAN Wave remains disabled until a separate public-network latency/drift/reconnect/media-authority
gate is accepted and passed.

## 10. Privacy, logging and retention

Invitation bearer, refresh/access material, device private key, raw source address and private
origins are never logged, exported or placed in analytics. Display name is personal data and appears
only in authorized account views. Routine audit stores generated IDs, action, safe terminal state,
actor ID and timestamps; it never stores raw request bodies.

Expired/cancelled invitation evidence is retained at most 30 days. Registration receipts follow the
bounded session replay window. Rate windows are removed within 24 hours after expiry. Provisioning
links remain while the account exists because they authorize only the narrow OWNER lifecycle view.

## 11. Deterministic state machine

`ACTIVE` invitation may transition once to `CONSUMED`, `CANCELLED` or `EXPIRED`. Only first valid
redemption creates account authority. `CONSUMED` plus an exact live receipt permits response replay,
not another creation. An invited account transitions `ACTIVE -> DISABLED`; PA1 defines no reverse
transition. Unknown persisted states are preserved and fail closed.

## 12. Acceptance record

The user accepted the high-level invite-only/public-access direction on 2026-08-31 and explicitly
accepted this completed v1 contract, ADR-045 and the exact limits/authority above on 2026-09-01.
PA1 is PASS. The same message explicitly activated PA2; it does not authorize PA3 deployment.

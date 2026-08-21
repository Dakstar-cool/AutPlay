# AutPlay Profile and Pairing Security Contract v1

Status: **ACCEPTED / DRAFT_NOT_IMPLEMENTED**
Milestone: Product M5A
Contract version: `v1`
Accepted by the user: 2026-08-20

## 1. Scope and authority

This accepted contract defines discovery, first trust, invitation, Android enrollment, device-bound sessions,
capability negotiation, connection lifecycle and the optional materialization of standalone local
intent. It is a proposal only: no route, migration, UI, key storage or deployment is implemented by
M5A.

The existing P03 session rules, P04 event hashes, P09 cursor semantics, owner authorization and
ADR-018 immutable materialization remain authoritative. `server_profile_id` is a device-local
wrong-profile guard, never an account, server identity or authorization principal.

## 2. Canonical identities

| Concept | v1 identity and rule |
| --- | --- |
| Local mode/persona | No active server binding. Local UUIDs and `local_mutation_outbox` remain valid; no synthetic `user_id` is invented. |
| Account/User | Pair `(server_instance_id, user_id)`. The current role is read from mutable `user_account`, never trusted from cached UI or an invitation. |
| Android connection/profile | Local `server_profile_id`; scopes settings, credentials and projections on one Android installation only. |
| Logical device | Server `device_id` plus a non-exportable P-256 device proof-of-possession key. Reinstall or key loss creates a new logical device. |
| Physical handset | Friendly description only. IMEI, Android ID and hardware serial are forbidden. |
| Session | `session_id` bound to `user_id`, `device_id`, parent/family and monotonic rotation generation. |
| Server instance | Persistent UUID `server_instance_id`, P-256 identity public key thumbprint and monotonic `identity_epoch`. |
| Origin | Normalized HTTPS API/stream URI. It is sensitive non-secret configuration, not identity or authority. |
| Friendly label | Display-only user text; never trust evidence. |

Account switching and account creation are outside v1. Enrollment attaches another device to one
existing active account and cannot select or elevate a role.

## 3. Protocol states

```text
NOT_CONNECTED
  -> CHECKING_DISCOVERY
  -> AWAITING_TRUST_CONFIRMATION
  -> AWAITING_INVITATION_CONFIRMATION
  -> EXCHANGING_INVITATION
  -> CONNECTED
  -> MATERIALIZATION_REVIEW (optional)

Any state -> CANCELLED
Any remote state -> UNAVAILABLE | AUTH_ATTENTION | INCOMPATIBLE_VERSION | IDENTITY_CHANGED
CONNECTED -> SYNC_PENDING when durable local work is waiting
```

Discovery, transport/application trust, invitation authority, enrollment exchange, session use and
capabilities are distinct checks. Success at an earlier stage never grants authority at a later one.

Every asynchronous result carries an immutable flow snapshot containing: generation UUID,
normalized origins, expected instance/key/epoch, transport evidence, expected account,
`server_profile_id`, device-key thumbprint/name, exchange operation ID and consent selection. QR or
origin change, Back, Cancel or an ordinary Retry creates a new generation. A reducer applies a
result only on a complete snapshot match. Exact lost-response replay is the sole operation that
reuses its original generation and operation ID.

## 4. Discovery and trust

### 4.1 Public discovery

`GET /api/v1/pairing/discovery` returns at most 16 KiB of signed metadata: contract version, instance UUID,
identity key/epoch/thumbprint, friendly label hint, normalized API/stream origins, supported API
majors, enrollment availability and expiry. It contains no invitation, access or refresh secret and
grants no authority.

A discovery QR contains the same non-secret compatibility hints and a fingerprint obtained through
an out-of-band owner-controlled channel. It is not an HTTP URL and not authentication.

### 4.2 Transport and application identity

Release builds transmit invitation or session credentials only over HTTPS with platform-trusted TLS
or an explicitly confirmed SHA-256 SPKI pin for self-managed private TLS. Cleartext is prohibited.
Debug/QA may enable HTTP only for loopback or RFC1918 trusted-LAN testing behind an explicit unsafe
development flag; this is never production/WAN evidence.

Application identity uses a persistent P-256 signing key. Signed discovery and capability envelopes
bind their canonical RFC 8785 payload to `server_instance_id`, public key, `identity_epoch`, issued
time and expiry. Signatures are ECDSA P-256/SHA-256 in fixed 64-byte IEEE P1363 form, base64url
without padding. Public-key thumbprints and payload hashes are lowercase SHA-256.

The first trust screen shows owner-provided fingerprint, server label and origin. An unverified
network response cannot supply the accepted fingerprint. Friendly label changes do not affect
trust. Origin changes under the same identity require renewed transport validation and explicit user
confirmation. HTTPS-to-HTTP downgrade is rejected.

V1 has no remote application-key rotation protocol. Any application-key or identity-epoch change is
`server_identity_changed` and blocks the connection. Recovery requires removing the old inactive
trust binding, treating the restored server as a new trust ceremony and re-enrolling; continuity is
never inferred. Missing old key after restore never causes silent key regeneration. Platform-PKI
certificate renewal is allowed when application identity is unchanged; SPKI-pinned deployments
require explicit re-trust after a pin mismatch.

Origin normalization is deterministic: lowercase scheme and IDNA-ASCII host, canonical bracketed
IPv6, remove the default port, normalize an empty or `/` path to no path, and reject userinfo,
non-root path, query, fragment or percent-encoded host. Release accepts only `https`; the unsafe
debug exception is applied after normalization. API and stream origins are normalized separately.

Cryptographic inputs are domain-separated. `payload_sha256` is SHA-256 over RFC 8785 bytes of the
`payload` object. Exchange/rotation `request_sha256` is SHA-256 over RFC 8785 bytes after removing
the hash and signature fields. ES256 signs the ASCII domain separator plus the raw 32-byte hash:
`AutPlay discovery v1\n`, `AutPlay capabilities v1\n`, `AutPlay enrollment exchange v1\n`, or
`AutPlay session rotation v1\n`. A key thumbprint is lowercase SHA-256 over the DER
SubjectPublicKeyInfo bytes. No JSON reserialization is accepted during verification.

## 5. Invitation and enrollment

### 5.1 Actors and limits

- The existing local CLI bootstraps the first owner exactly as in P03.
- An authenticated OWNER or ADMIN may issue an invitation for their own existing active account.
- A local CLI recovery command may issue one for an explicitly selected existing active account.
- Invitation issuance never creates an account, chooses a target role or changes authorization.
- Default TTL is 10 minutes; server-enforced maximum is 30 minutes.
- At most 5 active invitations exist per account and 10 may be issued per actor per rolling hour.
- The server stores only SHA-256 of the 256-bit invitation bearer secret.
- Cancellation, expiry and first success are terminal and audited with sanitized identifiers.

The enrollment QR is a distinct secret envelope. It contains the invitation ID, bearer secret,
normalized origins and the owner-provided instance identity/epoch/thumbprint,
but never access/refresh credentials. UI must suppress clipboard/history and warn against sharing or
screenshots. The secret is rendered once and is absent from routine logs, errors, exports and
diagnostics.

### 5.2 Confirmation and exchange

Before exchange, Android immutably confirms server label, instance thumbprint, account display name,
device name and one local-data choice: `Keep only on this phone`,
`Review and connect this library to <account>`, or `Cancel`.

Android creates a non-exportable P-256 key and sends a canonical exchange request containing
`exchange_id`, invitation credential, expected server/account, device public key and thumbprint,
normalized API/stream origins, a local `binding_commit_id`, device metadata, client nonce and a
signature proving possession. The server recomputes all
bindings, current role/account/device eligibility and invitation state in one transaction.

The invitation is intentionally a short-lived bearer: its first valid exchange chooses and binds the
device key. A stolen unused QR can therefore win the race; this shoulder-surfing risk is explicit and
bounded by TTL/issuance/cancellation. The exchange atomically consumes the invitation, creates the
logical device/session and records the bound key in its receipt. Concurrent exchange has exactly one
durable success. Wrong server/account, expired/cancelled invitation, or a used-invitation replay with
a different key/request returns the same non-disclosing `enrollment_invitation_unavailable` error.

A lost response may be replayed only with the same `exchange_id`, RFC 8785 request hash and proof by
the same device key. It returns a newly minted access token for the same successor session and the
same client-provided refresh secret; it never creates another device/session. The server never stores
recoverable invitation or refresh bearer values.

Exchange and rotation receipts contain only operation/request hashes, public-key evidence and
session lineage. They remain available through the session family's absolute refresh expiry (at
most 90 days from initial enrollment) plus five minutes of clock-skew grace. Exact replay is accepted
only while `now < receipt_expires_at`. At the boundary it returns terminal
`enrollment_invitation_unavailable` or `session_revoked` with no additional create/revoke side effect.
A bounded cleanup deletes an expired receipt no later than 24 hours after the grace boundary.
Before minting any access token on an exact replay, the server locks the receipt and successor rows
and reloads the bound account, device and successor session. Disabled/deleted account, revoked
device, or revoked/expired successor returns `auth_attention_required`, `device_revoked` or
`session_revoked` without a bearer and without changing durable state. A receipt is replay evidence,
never authorization and never a way around logout or revocation.

## 6. Device-bound session lifecycle

P03 legacy/bootstrap sessions keep their frozen behavior: access JWT lifetime is at most 15 minutes,
opaque refresh lifetime at most 90 days absolute, only SHA-256 refresh hashes persist, and replay of
an old generation revokes active sessions for the device.

M5-enrolled rotation v2 is additive under proposed `POST /api/v1/account/sessions/rotate`; it does
not overload the legacy P03 `/auth/refresh` route. Android creates the next 256-bit refresh secret locally and
sends only its hash in a device-key-signed request with `rotation_id`, parent session/generation and
canonical request hash. The server atomically revokes the parent and creates one successor. An exact
signed lost-response replay returns a fresh access token for that successor; a changed operation,
hash or key follows the P03 replay response and revokes the device.

Authorization for every protected operation reloads mutable account, device and session state.
Disabled/deleted accounts, revoked devices, revoked/expired sessions and stale role caches fail
closed.

### 6.1 Authorization matrix

All Android routes derive actor `user_id`, `device_id` and `session_id` from the verified current
session. No command accepts a target `user_id`; cross-account enumeration or mutation is always
`unauthorized`. OWNER/ADMIN does not create an Android cross-account administration scope.

| Operation | Actor | Target scope |
| --- | --- | --- |
| capabilities, list devices, list sessions | Any ACTIVE OWNER/ADMIN/USER with active device/session | Actor's own account only; bounded lists |
| create/cancel invitation | ACTIVE OWNER or ADMIN | Invitation whose `user_id` equals actor `user_id`; cancel is allowed to its issuer or same-account OWNER |
| exchange invitation | No existing session; valid invitation bearer | Exact invitation account/instance; first success binds supplied device key, later different-key replay is unavailable |
| rotate session | Current refresh plus matching device-key proof | Exact parent session/device/account only |
| logout current | Any active session | Exact actor session only |
| logout all | Any ACTIVE account role | All sessions whose `user_id` equals actor `user_id`, including current |
| revoke device | Any ACTIVE account role | Device whose `user_id` equals actor `user_id`; revoking current device is allowed and terminal |

Target absence, foreign ownership and inaccessible terminal state use non-disclosing errors. Audit
records the verified actor and server-resolved target, never caller-supplied ownership.

Lifecycle commands are distinct:

| User action | Remote meaning | Offline/timeout behavior | Local state |
| --- | --- | --- | --- |
| Disconnect locally | No remote assertion | Always possible after explicit confirmation | Delete this profile's credentials and active binding; retain library/outbox/media and non-active trust bookmark. |
| Log out this session | Revoke current session | `remote_outcome_unknown`; do not claim success | Clear credentials only after a proved terminal response. |
| Log out all sessions | Revoke every account session | `remote_outcome_unknown` | Same rule; other devices become unauthorized. |
| Revoke device | Revoke target device and its sessions | `remote_outcome_unknown` | Set-like duplicate is success only after authoritative response. |

Server terminal logout/revoke commands are set-like and idempotent under the same operation ID.
Duplicate authoritative requests return the existing terminal state. Audit records actor, target,
action, request ID and bounded reason code, never secret material.

## 7. Capability negotiation

Authenticated `GET /api/v1/profile/capabilities` returns a signed envelope bound to instance/key/epoch,
`user_id`, `device_id`, API/product versions, monotonic `capability_revision`, expiry, supported
optional operations and numeric limits. The canonical document is at most 64 KiB, has at most 64
operation entries and expires within 60 minutes.

Android caches original signed bytes, SHA-256 and the highest accepted epoch/revision. Expired cache
may explain prior UI state but cannot authorize a new remote operation. Unknown additive fields are
preserved for round trip and ignored by the executor. Unknown optional operations are not shown.
Unknown required features or an incompatible API major affect only the server connection; local mode
continues. A lower epoch/revision is `capability_rollback_detected`; v1 recovery removes the inactive
trust binding and performs a new trust/enrollment ceremony rather than accepting a network rotation
statement. Public discovery capabilities never grant authority.

## 8. Local data and materialization

Enrollment and local-data materialization are separate commits. `Keep only on this phone` leaves all
standalone intent in the outbox. `Review` opens a bounded selection of existing `local_change_id`
values; it is not import-all.

Immediately before materialization, Android revalidates generation, normalized origins, server
instance/key/epoch, `server_profile_id`, account, device key/ID, current session, consent and selected
local change IDs. The existing Room transaction then creates new Journal events with new IDs,
sequence and hashes and links them to outbox rows. Existing owner IDs, profile IDs, events, hashes,
sequences and payloads are never rewritten. Cancel, binding change or stale result creates no event.

## 9. Stable errors and recovery copy

| Code | UI state/copy intent | Recovery |
| --- | --- | --- |
| `not_connected` | Not connected | Scan discovery or enter an origin. |
| `server_unavailable` | Server unavailable | Keep local mode; retry with a new generation. |
| `transport_trust_required` | Verify this server | Compare owner-provided fingerprint. |
| `server_identity_changed` | Server identity changed | Stop; v1 requires explicit removal, new trust and re-enrollment. |
| `incompatible_api_major` | App/server versions are incompatible | Update the compatible side; local mode remains. |
| `capability_missing` | This server does not support the action | Hide/disable only that remote action. |
| `capability_rollback_detected` | Server capability rollback detected | Stop remote operations; administrator recovery required. |
| `enrollment_invitation_unavailable` | Invitation cannot be used | Ask an owner/admin for a new invitation. |
| `enrollment_rate_limited` | Too many invitations | Retry after the bounded server hint. |
| `stale_flow_generation` | A newer connection attempt exists | Ignore the delayed result. |
| `auth_attention_required` | Sign-in needs attention | Re-enroll or use owner recovery; do not erase local data. |
| `device_revoked` | This device was revoked | Clear active credentials after authoritative evidence; retain local data. |
| `session_revoked` | This session ended | Re-enroll; retain local data. |
| `remote_outcome_unknown` | Server result is unknown | Retry exact operation or disconnect locally; never claim revoke/logout. |
| `materialization_binding_changed` | Connection changed before applying | Review again; no event was rewritten. |

Errors use a stable code, safe human message, request ID and bounded retry hint. They exclude bearer
values, signature bytes, private origins, raw paths, search queries and personal payloads.
Every invitation, exchange or rotation response, including errors, carries
`Cache-Control: no-store` and `Pragma: no-cache`; clients and intermediaries must not persist it.

## 10. Threat, privacy and redaction model

| Threat | Required mitigation | Residual risk |
| --- | --- | --- |
| LAN/WAN interception | HTTPS in release; optional confirmed SPKI pin; application signature | User-controlled private PKI operations remain deployment-specific. |
| Discovery/origin substitution | Out-of-band fingerprint, signed instance evidence, explicit origin confirmation | A user who confirms the wrong fingerprint can trust an attacker. |
| QR shoulder surfing | 256-bit one-time secret, 10-minute default TTL, bounded issuance, cancel/audit, no clipboard/history | An unused bearer QR can be stolen within its TTL. |
| Concurrent/replayed exchange | Atomic consume, device PoP, operation/request binding, mutable successor authorization recheck, non-disclosing errors | Hash/public-evidence receipts persist through absolute session expiry plus bounded cleanup but grant no authority. |
| Capability downgrade | Signed epoch/revision and local high-water | Backup rollback needs explicit administrative recovery. |
| Lost server identity key | Fail startup or explicit re-trust; never regenerate silently | Recovery ceremony is operationally manual. |
| Lost response | Client-generated successor secret/hash and exact signed replay | Legacy P03 devices remain fail-closed rather than idempotent-v2. |
| Stale async result | Immutable generation snapshot and complete-match reducer | Cross-store crash recovery requires matching commit markers. |
| Local intent takeover | Explicit review plus last-moment binding/consent revalidation | User can still intentionally attach selected intent to the wrong visible account. |

Classification:

- **Secret, never exported/logged:** invitation bearer, access/refresh credentials, device/server
  private keys, raw authorization headers, recovery material.
- **Sensitive non-secret, redacted from routine telemetry/diagnostics:** private API/stream origins,
  SPKI pins, instance/account/device/session identifiers, public keys/signatures, request IDs when
  linkable, server labels, network addresses.
- **Personal payload, excluded from auth audit/export by default:** paths, filenames, media metadata,
  search text, library contents and local-change payloads.
- **Allowed bounded audit facts:** action code, outcome/reason code, timestamps, actor/target opaque
  IDs under restricted administration and counts/limits. No routine export includes raw IDs.

Process recreation stores only a non-secret checkpoint in versioned DataStore. Secrets and pending
rotation values remain in Keystore-protected storage. Room and WorkManager input contain neither
tokens, private origins nor enrollment payloads.

## 11. UI action to proposed API traceability

| UI action/state | Proposed command/query | Required failure handling |
| --- | --- | --- |
| Check entered/scanned server | `getDiscoveryMetadata` | unavailable, transport trust, incompatible major, identity changed |
| Confirm server trust | local trust-store commit after signature/transport verification | stale generation, identity/origin changed |
| Create/cancel invitation | `createEnrollmentInvitation` / `cancelEnrollmentInvitation` | unauthorized, rate limited, unavailable, already terminal |
| Confirm pairing | `exchangeEnrollmentInvitation` | unavailable invitation, stale generation, wrong trust binding, unknown outcome |
| Show Profile server/account | `getCapabilities` plus authenticated account/session claims | auth attention, capability missing/rollback/incompatible |
| Show connected devices/sessions | `listDevices` / `listSessions` | unavailable, unauthorized, revoked current session |
| Refresh an M5 device session | automatic `rotateDeviceSession` | exact lost-response replay or fail-closed device revocation on changed replay |
| Log out current/all | `logoutCurrentSession` / `logoutAllSessions` | remote outcome unknown; local disconnect offered separately |
| Revoke a device | `revokeDevice` | unauthorized, duplicate terminal, remote outcome unknown |
| Disconnect locally | local credential/binding delete command | no remote-success claim; local data retained |
| Review/connect local library | local bounded review then existing materialization transaction | cancel/stale/binding changed produces no event |

Unsupported account switching, registration, password management and Web Admin authentication stay
absent from UI.

## 12. Future implementation impact (not delivered)

M5B may add PostgreSQL `account.server_instance`, `account.enrollment_invitation`,
`account.enrollment_exchange_receipt` and `account.session_rotation_receipt`, plus additive
lineage/operation/request-hash fields for `account.user_session`. Existing
`account.device.public_key` becomes the device PoP public key.
Server private keys remain in a secret file/store; PostgreSQL contains only public evidence and
hashes. Receipt cleanup retains exact replay evidence through absolute session expiry plus five
minutes and deletes it no later than 24 hours after that grace boundary.

Android may extend versioned DataStore connection/checkpoint data, the Keystore credential envelope
and a separate device-key alias. Non-secret and secret stores require the same `binding_commit_id`
for deterministic crash recovery. No M5A Room migration is proposed: Room v12, Journal, cursor and
event hash formats remain unchanged.

The draft OpenAPI is a separate profile/pairing document. It does not overload the P04 sync API.
Its proposed pairing/profile/account paths also do not redefine the existing P03
`/auth/refresh`, `/auth/logout`, `/auth/logout-all` or `/devices/{device_id}/revoke` routes.

## 13. Deliberately deferred choices

Production domain, certificate authority, reverse proxy, VPN, hosting provider, public registration,
password login/recovery, account switching, Web Admin credentials and a separate auth service remain
deferred. This contract and ADR-029/ADR-030 were explicitly accepted on 2026-08-20. M5B is eligible
only after a separate explicit activation request.

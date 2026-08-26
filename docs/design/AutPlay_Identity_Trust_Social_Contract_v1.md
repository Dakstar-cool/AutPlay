# AutPlay Identity, Trust and Social Contract v1

| Field | Value |
| --- | --- |
| Status | ACCEPTED / CONTRACT_ONLY / NOT_IMPLEMENTED |
| Version | 1.0 |
| Milestone | Post-MVP S1A |
| Date | 2026-08-25 |

## 1. Scope and authority

This contract adds a design boundary for Web-approved Android admission, durable exact-key trust,
same-personal-server friendship, privacy-bounded presence, one-tap friend invitations to existing
Wave Rooms and capability-limited guest Room access. S1A changes no production route, PostgreSQL or
Room schema, Android UI or WebUI.

The accepted M5 profile/pairing contract, M6 browser-security contract and P13 Wave protocol remain
authoritative. A social relationship never grants account, device, session, library, Vault,
administrative or Room authority. Android local library and playback remain available without a
synchronous server request.

Profile listening statistics and their sharing policy are deliberately outside S1A. A later
privacy contract may use an accepted friendship edge as one authorization predicate, but it must
remain default-private and must not infer publication from friendship, presence or Room membership.

## 2. Canonical concepts

| Concept | Identity and authority | Explicitly does not imply |
| --- | --- | --- |
| Account | Server-scoped user UUID plus current mutable status and role | Friend, trusted device or Room membership |
| Logical device | M5 device UUID bound to one exact proof-of-possession public key | Person, friend or physical hardware identity |
| Trusted key | Account-scoped, administrator-approved exact public-key evidence | Active device, session or authorization after revoke |
| Device session | Current M5 authenticated account/device/session authority | Friendship or Room membership |
| Friendship | Accepted unordered pair of two active accounts on one server | Library, Vault, device, session or administration access |
| User block | Directed social deny edge from blocker to target | Device-key block or remote Room kick |
| Presence | Expiring, coarse, opt-in aggregate derived from fresh device heartbeats | Durable activity history, listening status or authorization |
| Room invitation | Expiring intent for one exact room and target friend account | Membership until an authenticated target device accepts |
| Room participant | Ordinary P13 account/device membership in one room | Friendship, media grant or account-wide access |
| Guest | Expiring capability principal for one room and allowlisted actions | Account, friend, library, sync, search, recommendation or Vault authority |

V1 has no federation, global handle, public user/Room directory, contact upload, public social
search or hosted short-code resolver.

## 3. Web-approved Android admission

### 3.1 Preconditions and request document

Android first completes M5 discovery, transport verification and explicit application-identity
trust. It then generates or reuses its non-exportable M5 P-256 key and submits a targetless
canonical signed request containing request UUID, server identity and epoch, exact
key/SPKI/thumbprint, nickname, bounded platform/model/app/API metadata, client nonce and request
time. IMEI, Android ID, hardware serial, advertising ID and invasive fingerprints are forbidden.

The server returns a random 128-bit Web review locator once. The locator resolves only this
sanitized request and cannot enroll a device, choose an account, create a role or mint a session.
There is no global pending-request list. The approving user enters or scans the locator inside an
already authenticated M6 Web session; a valid decision can bind it only to that Web actor's own
active account. Android explicitly confirms the returned account label and UUID before exchange.

The request grants no account, role, session, device row, media or status authority. Default expiry
is 15 minutes and the server-enforced maximum is 30 minutes. At most one pending request may exist
per server and exact key, at most five located requests may be under review by one Web account, and
at most 30 requests may be submitted per exact key per day. Source abuse limits allow ten submits
per 15 minutes and use short-lived HMAC-derived keys rather than persisted raw IP addresses.

If the one-time creation response is lost, Android does not create a second pending request and the
server does not retain plaintext recovery material. A bounded recovery POST proves the same request
UUID, canonical request hash, trusted server binding and exact device private key with a fresh
recovery nonce. In one transaction it invalidates the previous review-locator and poll-bearer hashes,
issues a new independent pair and returns that generation once under `Cache-Control: no-store`.
Recovery is available only while the request is pending, no more than three times and no faster than
once every two seconds. Old generations become non-disclosing and unusable before the new response
is committed; a lost recovery response may repeat the same rotation ceremony within those bounds.

### 3.2 Proof, comparison and polling

The server verifies M5 instance binding, canonical request hash and device-key proof before storing
hash/public evidence. A 12-decimal authentication string, displayed as three groups of four digits,
is derived from the RFC 8785 canonical serialization of the exact JSON array
`["autplay:s1a:admission-sas:v1", server_instance_id, request_id, request_sha256,
device_key_thumbprint_sha256, counter]`. The thumbprint uses the accepted M5 lowercase hexadecimal
SHA-256 DER-SPKI representation and `counter` is an unsigned integer starting at zero. SHA-256
hashes each canonical array; the first 40-bit big-endian prefix below 1,000,000,000,000 is accepted
and rendered as 12 zero-padded digits. Rejection sampling avoids modulo bias. It is comparison
evidence, never a bearer or authorization secret, and the client never supplies it.

Android receives a separate random 128-bit polling bearer once; PostgreSQL stores only hashes of
the poll bearer and review locator. Polling is no faster than once every two seconds, returns at
most the request's coarse state and expiry before approval, and returns the approving account only
for the exact approved request so Android can confirm it before exchange. Unknown, foreign and
terminal-unavailable requests share a non-disclosing result. Raw poll/review material is absent
from logs, URLs, WorkManager, Room, exports and diagnostics.

### 3.3 Web decisions and exchange

Every Web review begins with a no-store POST containing the exact locator; the server consumes it
into an opaque review binding scoped to the current M6 session. The raw locator never appears in a
URL. Every decision requires that binding and uses the accepted M6 active actor, exact Origin, CSRF
token, operation UUID, canonical request hash, PostgreSQL idempotency and audit boundaries.
Approval targets the current Web actor and no target-account parameter is accepted.
The UI displays sanitized nickname/model/platform, app/API version, request/expiry time, the
comparison string and transient restricted risk context.

| Decision | Durable meaning |
| --- | --- |
| Approve once | Authorize one M5-compatible exchange for this request and exact key; create no trust row |
| Trust device | Authorize the same exchange and create active exact-key trust only inside the successful enrollment transaction |
| Reject | Make only this request terminal; a later distinct request remains possible |
| Block device | Make the request terminal and create a directed account/exact-key block until explicit unblock |

Approval never renders an M5 enrollment bearer in HTML. Android completes an exact-key
proof-of-possession exchange using the approved request. The transaction rechecks request state,
account status, server identity, exact key and decision; then reuses M5 device/session issuance and
receipt rules. Exact replay converges on one device/session. Changed request/key replay fails
closed. Approval expires with the request and cannot be transferred to a new installation or key.

## 4. Trusted-key lifecycle

An active trust record is keyed by target account and exact SHA-256 public-key thumbprint and stores
the SPKI, approval evidence, monotonic revision and timestamps. Nickname, model, IP and physical
similarity are never trust keys.

Key-continuity re-enrollment requires a fresh server nonce, proof by the same private key, current
active trust, active account, no exact-key block and the normal M5 identity/origin checks. It may
mint only a new ordinary M5 device/session for the same account. A reinstalled app with a new key
must request admission again.

Remove trust disables future trust-based re-enrollment but does not revoke a current device or
session. Revoke access uses the existing M5 device/session path and does not remove trust unless the
user explicitly selects both consequences. Blocking the exact key denies new admission and
re-enrollment but cannot claim remote session revocation. Unblock restores request eligibility, not
trust. All commands are exact-operation idempotent and reload mutable authority before returning a
terminal result.

## 5. Friendship and social contact

A user may explicitly share a signed non-secret social contact card containing server instance,
user UUID, bounded display-name hint, issued time and expiry of at most 30 days. It is an app
document, not an HTTP URL, handle, directory listing or bearer. The receiver still authenticates as
their own account and sends a friend request to the exact card account.

Friend request, accept, cancel, decline, remove, block and unblock are server-scoped, bounded and
idempotent. A request expires after at most 14 days. Simultaneous opposite requests remain pending;
they do not silently auto-accept. Friendship becomes active only after the target performs an
explicit accept operation. The current friendship is one unordered account pair; request and audit
evidence retain actor direction.

A directed user block wins every friend request/accept race, cancels pending social and Room
invitations in both directions, removes the friendship and masks the relationship from the blocked
account. It does not reveal whether the blocker, account or prior request exists. Device-key block
and user block are separate types and cannot be substituted.

Friend removal prevents new friend invitations but does not rewrite existing P13 membership or
timeline. Existing shared-room participation stays visible until ordinary P13 leave/close/expiry.

## 6. Active-Room block boundary

P13 has no accepted remote kick command. Therefore a user block never pretends to eject the target.
If blocker and target currently share any active Room, the block command makes no social mutation
and returns active_room_exit_required with only a bounded room count. The UI must first execute the
ordinary durable P13 workflow: a non-host leaves; a host explicitly transfers or closes and then
leaves. After snapshots prove no shared active membership, the user re-confirms block.

This two-step policy is deliberately less convenient than an inferred kick. It preserves the
ordered Wave timeline, makes consequences visible and prevents a block response from hiding
unchanged membership. A future kick/remove command requires a separate accepted Wave ADR.

## 7. Presence

Presence publication and friend invitation availability are separate account settings and both
default to false. An independent room-activity-sharing setting also defaults to false and controls
whether `IN_ROOM` may be shown instead of `ONLINE`. Clients mutate only these settings and send
authenticated per-device heartbeats; they never submit aggregate presence or `fresh_until`.
Presence visibility is FRIENDS_ONLY in v1. It never publishes current track, queue, library, device
identifier, IP, exact last-active time or Room ID.

An authenticated device may heartbeat at most once every 30 seconds. A heartbeat is fresh for 90
seconds and is bound to the current account/device/session. Session existence or a stale row never
means online. PostgreSQL derives one friend-visible aggregate in this precedence:

1. OFFLINE when publication is disabled or no heartbeat is fresh;
2. IN_ROOM when room-activity sharing is enabled and any fresh device has ordinary active P13 membership;
3. AVAILABLE_TO_INVITE when invitation availability is enabled and any fresh device is eligible;
4. ONLINE otherwise.

The aggregate is a current projection, not history. Disposable notifications may hint that a REST
refresh is useful, but PostgreSQL and expiry remain authoritative. Cleanup removes expired device
rows within ten minutes. Multi-device aggregation exposes neither device count nor identifiers.

## 8. One-tap friend Wave invitations

Only the current authenticated P13 host device may create a friend invitation for one exact active
room and one exact active friend account. Creation rechecks both directed blocks, friendship,
target invitation setting, room state, host membership, capacity and existing membership. Default
TTL is ten minutes and cannot exceed room expiry. At most eight pending friend invitations exist
per room.

Acceptance requires an active target M5 account/device/session and an exact operation UUID. It
rechecks invitation state, friendship, both blocks, target setting, room epoch/status/capacity and
the accepting device's absence. Exactly one accepting device is materialized through the ordinary
P13 device-bound membership transaction. First concurrent success wins; exact replay converges;
another device or changed request receives a non-disclosing terminal result. Friendship never
bypasses P13 snapshots, sequence recovery, preflight or per-device media authorization.

Invite cancellation, expiry, block, friendship removal, account disable/delete, room close/full or
host loss before acceptance fails closed. Local library/playback remain usable on every failure.

## 9. Guest capability boundary for future S1D

S1A selects the smallest Android-first guest boundary. A guest document is a non-HTTP
autplay-guest-v1 QR/app document containing a random 256-bit bearer for one exact room. PostgreSQL
stores only its SHA-256, bounded role/action allowlist, expiry, optional use count and terminal
evidence. Default TTL is 15 minutes, never exceeds the six-hour room lifetime and is single-use by
default.

The raw bearer is forbidden in HTTP path/query, browser history, Referrer, clipboard, logs,
screenshots, analytics, crash reports, recent-task previews, exports and diagnostics. Android must
consume it from ephemeral app state, exchange it by an explicit no-store POST and clear the raw
input before normal navigation. Browser redemption and browser playback are not selected in v1.

The resulting guest principal can access only the named Room snapshot/live channel and the minimal
join, presence, preflight and timing actions accepted by S1D. It cannot call any account, profile,
device, session, friend, library, search, sync, recommendation, Vault or administrative API.

Guests may play only media independently available and authorized on their device under ordinary
P08/P13 source rules. A Room, host, friend or guest capability grants no audio bytes or Vault
access. Host-to-guest relay or room-scoped streaming grant requires a separate security/rights ADR
and is not authorized by this contract.

## 10. Privacy, retention, export and deletion

| Data | Retention and visibility |
| --- | --- |
| Admission request/review/poll hash | Pending through expiry; terminal public/hash evidence at most 30 days; exact-locator review and deciding-account administration only |
| Trusted key | Until remove/block/account deletion; public key evidence is restricted security data and excluded from routine export |
| Friend request | Pending at most 14 days; terminal idempotency/audit evidence at most 30 days |
| Friendship | Until remove/block/account disable/delete; visible only to the two active accounts |
| User block | Until unblock or account deletion; blocked account receives no block metadata |
| Presence heartbeat | Fresh at most 90 seconds; cleanup within ten minutes; never included in data export as history |
| Room invitation | Until terminal state or Room expiry, then terminal idempotency evidence at most 30 days |
| Guest secret hash/capability | Until room/expiry/revoke plus terminal evidence at most 30 days; raw secret never persists |

Account export may include the user's own friend relationships, sent/received request history still
inside retention, privacy settings and restricted security metadata with opaque counterpart IDs.
It excludes other users' presence history, device keys, IP/source context and raw social graph.
Account deletion immediately disables social actions, expires presence and invitations and removes
or pseudonymizes retained counterpart evidence according to existing security-audit policy. A
friend cannot use an export or stale cache to discover a deleted/blocked account.

Routine logs/metrics contain only bounded action, outcome, reason, timing bucket and counts. They
exclude raw user/device/session/room/request IDs, social graph, names, contact cards, comparison
strings, private origins, IP addresses, media metadata and guest/poll bearers.

## 11. Authorization summary

| Operation | Required actor and target |
| --- | --- |
| Submit/poll admission | Trusted server identity plus exact device-key proof; poll additionally requires the exact non-URL polling bearer and a fresh signed nonce |
| Recover lost admission creation response | Same pending request UUID/hash/server binding plus exact device-key proof and fresh recovery nonce; atomically rotate both secret hashes without a second request |
| Resolve/decide admission | Exact review locator consumed by no-store POST into an M6-session-scoped review binding; decision requires that binding and approval can target only the actor's own account |
| Re-enroll trusted key | Same account and exact active key proof; no user/key block |
| Manage trusted key | Same-account active M6 OWNER/ADMIN; revoke remains a separate M5 command |
| Friend request/accept/remove | Active M5 account/session; exact self/counterpart pair; no directed block |
| Block/unblock user | Active M5 account/session as blocker; active-Room precondition applies |
| Publish/read presence | Active M5 device/session for publish; accepted friend and privacy settings for read |
| Create friend Room invite | Active current P13 host device; exact active friend target |
| Accept friend Room invite | Active target account/device/session; ordinary P13 membership checks |
| Redeem guest document | Exact valid guest bearer plus accepted S1D proof/rate ceremony; one Room only |

Every absence, foreign owner and inaccessible terminal state uses a non-disclosing stable error.
Mutable account, key, friendship, block, invitation, room, device and session authority is reloaded
inside the committing transaction.

## 12. Stable failures

Required stable codes include admission_request_unavailable, admission_expired,
admission_rejected, device_key_blocked, comparison_required, trusted_key_unavailable,
friend_request_unavailable, friendship_required, user_blocked, active_room_exit_required,
presence_private, room_invitation_unavailable, room_full, room_changed, guest_unavailable,
guest_scope_denied, operation_conflict, rate_limited, auth_attention_required,
server_identity_changed and server_unavailable.

Offline/timeout never claims a remote mutation succeeded. Exact-operation retry is offered where a
durable receipt exists; otherwise the UI reports unknown/unavailable and preserves local behavior.

## 13. Draft implementation impact

S1A proposes, but does not implement, additive PostgreSQL tables for admission requests/decisions,
exact review/poll hashes, exact trusted keys/key blocks, friend requests/current friendships,
directed user blocks, social privacy settings, expiring device presence, friend Room invitations,
guest invitation hashes/capabilities and bounded operation receipts. PostgreSQL remains the only
social authority; no Redis, broker, graph database or new service is proposed.

S1B owns admission/trusted-key tables and M5/M6 adapters. S1C owns friend/presence/friend-Room-invite
tables and P13 integration. S1D owns guest tables only after separate activation. Migrations are
additive and downgrades must refuse while owned rows exist rather than delete them.

Android may later add profile-scoped Room projections for friends, privacy settings, current
aggregate presence and pending Room invitations. They are display/recovery caches, not authority,
and contain no bearer, raw contact/guest document, IP, exact heartbeat history or private origin.
DataStore may hold non-secret privacy preferences only after server acknowledgment; Keystore owns
any admission poll/guest bearer during its bounded flow.

The draft schemas under contracts/social/v1, the draft OpenAPI slice, social event envelope and
deterministic fixtures are marked DRAFT_NOT_IMPLEMENTED. They do not modify existing M5, M6, P04 or
P13 documents.

## 14. UI action and state mapping

Android admission states are request-ready, awaiting comparison, pending approval, approved and
exchanging, connected, rejected, blocked, expired, cancelled, unavailable and identity-changed.
WebUI adds Connection Requests and Trusted Devices surfaces with consequence-labelled decisions.

Android Friends exposes contact-card import/share, pending incoming/outgoing, friends, blocked,
private/offline/online/available/in-room presence and Room invite pending/accepted/expired/cancelled/
full/changed states. Unavailable server/social state never blocks Home, Library or local playback.
Friend removal and block clearly disclose the unchanged-room or exit-required policy.

Guest UI remains a future S1D surface and must show exact Room scope, expiry, independent-media
limitation and unavailable/revoked/full outcomes before redemption.

## 15. Acceptance decisions

Explicit S1A acceptance means accepting all of the following:

1. Web approval is limited to the actor's own account, exact request and exact device key; trust is
   created only with successful enrollment and is distinct from session/device revocation.
2. Friendship is same-server and explicitly mutual; presence and invitation availability are
   separate opt-in settings, coarse, friend-only and expiring.
3. One-tap Room invitation remains host-created and materializes only one ordinary device-bound
   P13 membership after all friendship/block/room/session checks.
4. User block has no inferred kick: shared active Rooms must be durably left/transferred/closed
   before block, while friend removal leaves existing membership visible and unchanged.
5. Guest v1 is Android app-document redemption only and guests play solely independently available
   media; browser guest access, relay and Room-scoped Vault grants remain deferred.
6. Public discovery, federation, hosted codes, contact upload, exact listening presence, profile
   statistics sharing and social recommendations remain outside S1A/S1C.

The user explicitly accepted these decisions and ADR-035/036/037 on 2026-08-25 after the final
independent review reached zero Critical/Major/Minor findings. S1A is PASS. S1B is eligible for a
separate explicit activation but does not start automatically.

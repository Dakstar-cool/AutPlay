# Self-device pairing v1

Status: implementation in progress; disabled until acceptance and quota integration.
Decision: ADR-053 and the accepted Admin/account onboarding plan.

## Authority and transitions

An ACTIVE Android account of any role may add a phone to its own account. The source must
have a live V2 session family. Ordinary session rotation preserves this family; logout,
device revocation, account suspension or authority-generation change cancels unfinished
authority. This protocol does not create permanent TrustedDeviceKey authority.

The state machine is OPEN -> CLAIMED -> APPROVED -> EXCHANGED. CANCELLED and REJECTED
are terminal. Expiry is derived from the fixed 15-minute deadline. A claim cannot change
its key. Approval requires matching the 12-digit comparison code on both phones. Exchange
requires explicit confirmation of the approved account on the new phone. Cancelling an
already exchanged ceremony cannot revoke the resulting independent device.

## Wire format

All requests are bounded UTF-8 JSON (8 KiB), reject duplicate/unknown fields, and include
`contract_version: "v1"`, `schema_version: 1`, `ceremony_id`, `requested_at` (UTC),
`expected_server_instance_id`, `expected_identity_epoch`,
`expected_identity_thumbprint_sha256`, `expected_api_origin`, `expected_stream_origin`,
and `request_sha256`. UUIDs and 32-byte SHA-256 values use canonical lower-case spelling.
Dates use UTC ISO8601. Hash is SHA-256 of RFC8785 canonical JSON omitting `request_sha256`
and `device_signature_b64url`. Signed requests use ES256 P1363 over ASCII operation domain
`autplay:self-device-pairing:<claim|poll|exchange>:v1\n` followed by the raw request hash.

| Method/path under /api/v1 | Additional request fields |
| --- | --- |
| POST /account/device-pairings | operation_id, rendezvous_secret_sha256 |
| GET /account/device-pairings/{id} | No body; source account/device/family only |
| POST /pairing/self-service/{id}/claim | claim_id, poll_secret_sha256, device_public_key_spki_b64, device_key_thumbprint_sha256, device_name, platform=ANDROID, app_version, device_signature_b64url |
| POST /pairing/self-service/{id}/poll | claim_id, claim_request_sha256, device_signature_b64url |
| POST /account/device-pairings/{id}/decision | operation_id, action=APPROVE/REJECT/CANCEL, expected_revision, claim_id (null before claim), claim_request_sha256 (null before claim), comparison_code (null except APPROVE) |
| POST /pairing/self-service/{id}/exchange | exchange_id, binding_commit_id, claim_id, claim_request_sha256, approval_operation_id, confirmed_account_id, next_refresh_token_sha256, device_signature_b64url |

The first, second and fifth endpoints require ordinary bearer authentication. Claim supplies
`X-AutPlay-Pairing-Secret` containing the rendezvous secret; poll/exchange supply the poll
secret in the same header. Both secrets are independent 32 random bytes encoded as canonical
43-character base64url. They never appear in URLs, logs or diagnostic exports.

The source generates and encrypts its pending start and rendezvous secret before sending.
QR is JSON, at most 4 KiB, with format `autplay-self-device-pairing`, version 1, ceremony ID,
rendezvous secret, the expected server identity/origins and the immutable expiry. The new
phone verifies signed discovery against this identity before submitting its generated key.
It encrypts the poll secret, key alias and exact claim before sending. QR authorizes only a
claim; it contains no account login, refresh token, recovery code or private key.

## Atomicity and replay

Start idempotency binds the operation and ceremony to account/device/V2 family, payload and
authority generation. Initial starts must be within two minutes of server time; replay
cannot extend the 15-minute expiry. Maximum three unfinished ceremonies per account.
Claim fixes one key/poll hash and produces a 12-digit SAS derived from SHA-256 domain,
server identity, ceremony, claim hash and key thumbprint. Its exact payload is an RFC8785
object with `server_instance_id` (UUID string), `identity_epoch` (integer), `ceremony_id`
(UUID string), `claim_request_sha256` (hex string) and `key_thumbprint` (hex string).
Compute SHA256(`autplay:self-device-pairing:sas:v1\n` ASCII bytes followed by SHA256 of
the canonical payload), read the first eight digest bytes as unsigned big-endian, take
modulo 10^12 and left-pad with zeros to 12 decimal digits. Bearer hashes are SHA-256 of
the canonical base64url ASCII spelling, matching existing M5 refresh conventions.
Compare-and-set decision receipts
bind actor, operation, action, revision and exact request. Changed reuse is a conflict.

The new phone persists exchange/binding IDs, the complete confirmed request and its own
successor refresh secret before sending exchange. The server stores only the refresh hash.
Exchange serializes server -> account -> ceremony -> device -> session and checks current
admission quota before inserting. Device, V2 family/session, terminal receipt and audit
commit together. Exact replay returns the same binding/device/session with a newly issued
short access token. It validates the resulting device/session and account generation;
it does not require the old phone to stay connected after commit. Revoking that resulting
session/device or changing account generation prevents minting another access token.

Rate gates use bounded PostgreSQL windows for source and server. Polling is at least two
seconds apart; Android backs off with jitter to at most 15 seconds and stops when backgrounded.
Polling/retry never extends deadlines. Failed possession/proof returns one sanitized
`self_pairing_unavailable`; payload errors, operation conflicts, stale revision, rate limit
and quota denials are bounded stable outcomes. No public enumeration of account identities.
Account ID/label is disclosed to the new phone only after the source approves its exact key.

Nonexchange evidence is eligible for bounded cleanup after expiry plus 24 hours. Successful
receipts remain until resulting session expiry plus five minutes. Cleanup does not revive
expired starts. Recovery revokes all unfinished ceremonies and increments account authority
generation; restoring a backup must apply the final account deletion evidence before serving.

## Acceptance

Verify USER and OWNER pairing, wrong account/device/family/key/SAS/account confirmation,
double claim/exchange, refresh/revoke/recovery races, response loss and process death,
fixed expiry/cleanup and quota lowering in real PostgreSQL. Android must retain local
library/Journal isolation, first-bind gate ownership and encrypted pending state. Physical
QR scanning and the laptop/M55 network admission remain separate acceptance evidence.

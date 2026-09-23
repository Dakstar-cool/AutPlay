# ML control plane v1: consent and artifact review

This contract refines Revision 15 section 7.5. It specifies an API boundary; no
route, challenge issuer, attestation verifier or WebAuthn review mutation is
enabled by this file or by migration `0061`.

## Participant serving-use consent

`PUT /api/v1/ml/sona-serving-consent` accepts exactly
[`sona-serving-consent-command.schema.json`](sona-serving-consent-command.schema.json).
The affected owner is the authenticated principal; a target owner identifier is
not accepted. This purpose is independent of R1B training/evaluation consent.
A `GRANTED` mutation requires an operation-bound challenge and per-use Android
step-up or an independently enrolled participant WebAuthn assertion. `DENIED`
and `WITHDRAWN` use a current valid session, carry `null` challenge ID and are
never delayed by reauthentication. Every mutation appends the independent
serving-consent ledger before projecting current consent into PostgreSQL. A
missing or unreconciled ledger head is no grant. This route and projection are
not implemented yet; the separate ledger currently supports only offline
initialization and head verification.

## Artifact decision command

The intended Web Admin mutation is `POST /admin/ml/artifacts/{artifact_sha256}/license-decisions`.
The body is exactly
[`artifact-license-review-command.schema.json`](artifact-license-review-command.schema.json).
The lower-case path SHA-256 must equal the body SHA-256. `reviewer_user_id`, role,
Web session, `reviewed_at`, decision sequence and new generation come from live
server authority, never from the body. Only current `OWNER` or `ADMIN` may review.
`LEGACY_UNREVIEWED` is an import state and cannot be requested as a review.

The review content records exact license text hash, restrictions, redistribution,
modification, attribution, reference, maximum offline revocation lag and output
disposition. The reviewed artifact's manifest and content hash remain immutable.
An approval requires a positive offline lag. A denial or revocation uses zero.
Every JSON integer is bounded by `2^53-1`; `expected_generation` is at most
`2^53-2` so its successor can be represented. JSON object duplicate keys,
non-finite numbers, alternate hash case, extra fields and payloads over the
32,768-byte bound are rejected before challenge issuance. Nested JSON depth is
at most 12. The strict parser also validates the path/body hash equality and
canonical lower-case UUID before deriving the request hash.

The canonical request hash is SHA-256 of the ASCII domain prefix
`autplay.ml.artifact-license-review.v1\0` followed by RFC 8785 bytes of
`{"action":"ARTIFACT_LICENSE_REVIEW","path_artifact_sha256":<path>,"command":<complete validated body>}`.
CSRF and WebAuthn assertion bytes are outside
the canonical command; they cannot alter its meaning. `(actor_user_id,
operation_id)` is the idempotency key. A retry with the same canonical hash
returns the committed receipt; reuse with another hash conflicts. The receipt
will contain actor role, administrative scope, before/after generation, reason,
state, decision sequence and a digest of the consumed step-up challenge. A
failure before commit leaves the challenge unconsumed and the current decision
unchanged. A decision append, derived-current update, challenge consumption and
audit receipt must commit together.

## Fresh WebAuthn proof

Challenge issuance requires the current authenticated Web session and CSRF.
The challenge holds a random 32-byte nonce hash, actor, exact Web session,
`ML_ADMIN_OPERATION_V1` purpose, `ARTIFACT_LICENSE_REVIEW` action, operation ID,
canonical command hash, expected generation, issue time and at most five-minute
expiry. The raw nonce is returned once and is not stored. The assertion is
verified against an existing non-revoked credential of that actor, the pinned
Web origin/relying party and user verification; its counter and backup flags
follow the existing M6 passkey checks. The server rechecks actor, session,
challenge and artifact generation under locks immediately before the review
commit. Session age, cookie possession, CSRF alone and an unrelated passkey
login are not fresh proof. A missing, expired, replayed, changed-body,
cross-session or cross-actor challenge fails closed.

## Face tuple read

An activation transaction locks each immutable artifact row and current
license row in sorted content-hash order, checks no unresolved migration
issue exists, and derives
the required-set digest, policy-list digest, minimum positive offline lease
and strictest output disposition. The activation commit must occur before
those locks are released. A later denial or revocation becomes live authority
for subsequent work and requires separate projection tombstoning; the frozen
policy digest is never a substitute for the current decision.

## Pending gate

The internal PostgreSQL review writer and tuple reader are deliberately
unwired. The HTTP endpoint, one-time WebAuthn assertion, operation receipt,
artifact import command, review audit and denial/revocation propagation are
required before any operator can change a license through the product.

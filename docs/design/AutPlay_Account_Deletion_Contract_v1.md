# Account deletion and cancellation v1

Implements ADR-053(6), including the user's 2026-09-18 decision to forbid deleting
the last ACTIVE, nondeleted OWNER. The protocol is separate from ordinary account
recovery. Local implementation is not authorization for production deletion.

## Suspension and proof

`GET /account/deletion` requires an ACTIVE V2 application actor. It reports the
current authority/code generations and whether a request can be made. Recovery
credentials must match the current server identity. The last effective OWNER is
blocked even if other OWNER rows are disabled or awaiting deletion. There is no
implicit ownership transfer or replacement bootstrap.

`POST /account/deletion` requires the same current actor, possession of the current
recovery code, the current device's P-256 key, exact server identity/origins, expected
authority/code generations and an explicitly confirmed account ID. Requiring the
code here establishes an available cancellation proof before all sessions end.
The code uses the recovery protocol's private header and is never echoed or logged.

The request transaction acquires identity, global admission, sync owner and account
locks in that order. It rechecks current authority, increments authority generation,
revokes devices/sessions/browser sessions/passkeys/trust/invitations/admissions and
resource grants, and records `DELETION_PENDING` plus one immutable request intent.
Its cancellation deadline is exactly 720 hours after PostgreSQL's sampled time.
Retained process executions remain charged until verified exit. Durable personal
and social data stay present; temporary invitations and presence are retired.

Before PostgreSQL mutates authority, the independently retained privacy ledger
records ATTEMPTED for the exact request and a digest of its complete immutable
private receipt. A PostgreSQL rollback keeps this conservative barrier. An exact
fresh retry may reuse its original acceptance time only when every receipt fact
still matches; elapsed time or ACTIVE status cannot prove nonacceptance.

The last-OWNER guard also runs in PostgreSQL under the shared serialized admission
lock. Concurrent retirement cannot remove the final effective owner. Administrative
DISABLED remains distinct; disabling a pending invited account creates a veto that
cancellation cannot override.

## Loss and cancellation

The client persists the exact request and proof material securely before sending.
After a lost request response, normal bearer authority is already revoked.
`POST /deletion/request-receipt` therefore accepts only the exact signed historical
request and its original code. It returns that intent's ID, dates and current phase;
it cannot create or extend an intent, issue credentials or resurrect a session.

`POST /deletion/request-resolve` accepts the same original signed bytes and code,
without bearer authority. An accepted operation returns its positive receipt.
Only a never-attempted operation strictly older than the 120-second acceptance
window can receive an immutable SEALED `NOT_ACCEPTED` decision. Its exact account,
operation and request hash prohibit all future acceptance. The eight-field negative
response grants no binding or credentials. Original proof can replay that decision
after code/identity rotation or later account purge. ATTEMPTED without a matching
PostgreSQL receipt remains attention-required; the server never guesses a negative.

Android queries the positive receipt first, resolves expired pending originals
using their exact retained bytes, and validates the negative's full timestamp/hash
binding before clearing only the identical encrypted journal under the shared gate.
It never restores detached credentials. Delayed replies and unsuccessful durable
clear remain unresolved and cannot clear a replacement operation.

`POST /deletion/cancel/preview` proves the code and a fresh device key before showing
the account label, exact request, revision and deadline for explicit confirmation.
`POST /deletion/cancel/commit` binds the confirmed account, request/revision, code
generation, next verifier, client-created refresh hash and binding commit ID.
The account must still be DELETION_PENDING, the intent PENDING, and database time
strictly before the deadline. Cancellation serializes with request/purge/disable.
It atomically returns the account to ACTIVE, rotates the code and installs one fresh
binding. Old authority stays revoked. Failure rolls back the entire transition.

If that response is lost, `POST /deletion/cancel/outcome` accepts the exact signed
cancel body and its already bound `X-AutPlay-Recovery-Refresh` secret. It verifies the
immutable CANCELLED intent, cancel operation/hash, rotated credential generation, new
device key and live result session, then returns that existing binding only. It cannot
cancel a pending request, create another session or revive an expired/superseded branch.

Cancellable ordinary replay records an immutable DELETE_CANCEL receipt in the existing
recovery receipt store. Exact old-code replay requires that same unexpired receipt,
current generation and live result binding. After receipt expiry/cleanup the old code
cannot create another branch; only the refresh-bound outcome repair above may return the
already committed exact result. The durable cancellation intent keeps only its
immutable operation UUID; the transition trigger verifies the proof receipt when
the transition occurs, without preventing normal one-day receipt cleanup.

Cancellation also records its exact operation/hash/time in the independent request
chain before PostgreSQL commit. An ambiguous rollback blocks receipt, cancellation
preview, purge and startup until the exact pending cancellation reconciles. Each
uncancelled ATTEMPTED entry reserves its future cancellation position in the bounded
journal. Restore validates both chains and all request intents, including owners
entirely absent from the restored database. Final completion requires the exact
immutable PostgreSQL purge receipt; an absent owner alone cannot manufacture it.

Requests are strict bounded JSON, at most 8192 UTF-8 bytes, with no duplicate keys,
unknown fields or nonfinite numbers. Canonical request hashing and P-256 P1363 proof
use the recovery encoding with distinct ASCII prefixes
`autplay:account-deletion:<request|preview|cancel>:v1\n`. Bodies never contain the raw
code. All responses are no-store. Schemas and shared test-only proof vectors live in
`contracts/openapi/v1/autplay-account-deletion.openapi.json` and
`tests/fixtures/account-deletion/v1/proof-vectors.json`.

## Final purge and restore requirements

PENDING may become noncancellable PURGING only at/after the deadline. Final purge
must first verify process/cleanup closure and any external legal hold. It must use
an explicit owner-data inventory and restricted privacy deletion authorization,
preserve shared catalog/Vault/history, and remove/redact indirect personal references.
Schema cycles and immutable evidence guards require narrow purge exceptions; no
global trigger disabling or generic recursive cascade is allowed.

Independent keyed deletion evidence must become durable outside the restored
PostgreSQL backup before final deletion. Its stable owner tag is domain-separated
`HMAC-SHA256("privacy-delete-v1", user_id)`; completion/protection times are metadata.
Evidence is retained until every pre-delete backup expires or is verified destroyed.
No raw owner ID or payload belongs in independent completion records. Interrupted
intent/evidence/purge steps must resume without claiming success early. A restore
must reapply outstanding deletions and verify zero owner rows before API/worker
startup. This never claims erasure from an offline or dead phone.

Migration `0053_privacy_purge` implements a frozen PostgreSQL deletion order and
transaction-local protected exceptions to immutable evidence guards. It refuses
holds, running jobs, unclosed byte processes and incomplete staging cleanup.
Shared catalog/Vault/embedding evidence survives with personal attribution removed;
owned recommendation inputs, recovery authority and indirect browser/social results
are removed. A final verification checks owner and captured authority IDs in UUID,
text, array and JSON fields before inserting an immutable completion receipt.

The filesystem ledger uses independently retained SQLite with a keyed append-only
chain, verified head and identity, serialized writes and EXTRA synchronization.
The database commits PURGING before external PREPARED evidence; the external
evidence becomes durable before the database purge. A lost completion acknowledgement
is reconciled from the immutable database receipt. Exact retries never append a
second deletion. Evidence is currently retained indefinitely; no expiry/reset API
is provided. Independent retention is an operator storage obligation, not protection
against rolling back the ledger itself together with PostgreSQL.

API, streaming, CPU, music, metadata and database-backed GPU workers run the shared
restore gate before serving or composing work. It reapplies verified evidence even
to an ACTIVE account in a pre-request backup. Production settings require ledger
configuration even when deletion switches are disabled. Missing, corrupt or
wrong-key evidence prevents startup and is never initialized automatically.

Offline first provisioning requires stopping and draining every legacy accepting
process/transaction before sampling cutover C from PostgreSQL `clock_timestamp()`.
The new signed immutable coverage identity uses that same freshness clock. Requests
at/before C+120 seconds cannot be declared unaccepted; readiness stays unavailable
through C+240 seconds to cover the permitted client timestamp lag. Legacy files or
untracked PostgreSQL requests fail closed. No automatic upgrade/backfill/reset is
provided. See the operator procedure and current verification in
`docs/release/ADMIN_DELETION_RESOLUTION_2026_09_19.md`.

Restored historical open processes/staging still block this gate: a trusted offline
closure/drain workflow remains required. Neither resource expiry nor restore
authorization fabricates process exit. Android deletion/cancellation is locally
implemented and reviewed; see `docs/release/ADMIN_ANDROID_DELETION_VERIFICATION_2026_09_18.md`.
Exact negative resolution and refresh-bound cancellation outcome repair are implemented;
physical device/server acceptance remains open. Shared-training deletion now installs an
irreversible publication tombstone for every PUBLISHED run containing the purged owner, so
restored artifacts cannot regain serving authority.
Feature enablement remains a local test option. See the operator notes and exact
verification scope in `docs/release/ADMIN_ACCOUNT_DELETION_2026_09_18.md`.

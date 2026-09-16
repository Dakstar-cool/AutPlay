# AutPlay resource admission v1

Status: implementation contract under review. The accepted product policy is
in `explorations/AutPlay_Admin_And_Account_Onboarding_Draft_v1.md` and ADR-053.
Controls remain unavailable until their enforcement paths are connected and tested.

## Ownership and limits

PostgreSQL owns policy, account overrides, queue order and leases. The initial
account defaults are five active application devices, two logical server
playbacks and two background transfers combined. Revoked devices and browser
credentials do not count. Local files, metadata sync, history upload, Vault
ingest/analysis and local imports do not consume audio transfer leases.

Per-device concurrency is bounded separately: one logical playback and at most
two transfers. Short current/next prebuffer overlap, Range requests and seek
belong to the same logical playback owned by AutPlayPlaybackService.
Downloads require a TRANSFER lease even though the audio endpoint is shared.
Stable IDs are created before I/O and survive ordinary operation retries:
play instance ID, download intent ID, upload intent ID, provider acquisition ID.
An Internet acquisition's local staging-to-Vault copy is the same transfer.

Global playback/transfer limits must be explicitly configured within deployment
ceilings derived from load measurements. An unconfigured global budget cannot
enable playback/transfer admission or publish a working editor. This does not
block the one-time OWNER bootstrap or its first application device. Zero never means unlimited.
The global policy row stores account defaults, global limits and a revision.
Account overrides are nullable per resource and retain their row/revision when
reset; deleting/recreating a row must not revalidate an old form (ABA).

Reducing a limit neither revokes devices nor interrupts active leases. Renewals
of an active lease remain valid under a lower limit. A new operation, or a lease
that expired before renewal, must obtain capacity under the latest policy.
Increasing limits and resetting inheritance take effect without a process restart.

## Serialization

All admission and policy mutations use the same transaction-level PostgreSQL
advisory lock. The order is server identity when needed, admission lock, account,
override/operation/lease rows, then existing ceremony/device/session/job rows.
Every device creation path acquires the admission lock before its account lock:
M5 invitation exchange, S1 admission/trusted reenrollment, PA2 registration,
bootstrap and USER self pairing. Exact binding receipts are checked before the
count/new insertion and consume no additional slot. Count and insertion share a
transaction; all active DeviceRows, including LEGACY, count.

Account suspension/recovery/revocation that changes lease authority follows the
same order. This includes M6 AdminCommandRepository.execute, M5 and generic
logout/revoke, trusted-key revoke and rotation replay-abuse branches. PA2 issue,
redeem, registration response and account disable must move their identity lock
before account/invitation/receipt locks. Cross-account commands lock actor and
target accounts in ascending UUID order after the common admission lock.
Bootstrap has no actor/session: admission lock, existing OWNER_BOOTSTRAP lock,
empty-owner check, then new account/device. The migration seeds policy defaults
with unconfigured global budgets; the operator supplies measured ceilings.

DEVICE_SESSION authority stores account authority_generation, device ID and
session family (LEGACY uses its exact session ID). Normal V2 rotation retains the
family; logout/reuse revocation terminates its leases. Android-initiated Internet
jobs retain that family in their durable acquisition intent. SERVER_ACQUISITION
authority instead binds account generation, real job/attempt fence and existing
source-authorization/policy revisions for A1 Web/manual/automatic acquisitions.
Only a server worker repository seam can create this authority; public acquire
cannot select it. It uses account/global TRANSFER capacity without a fabricated
device/session or per-device limit. Existing A1 admission rules remain mandatory.
Authorization is checked again in each short transaction. The global admission
lock is never held while reading request bodies, downloading or serving files.
Network transfers must not open a nested transaction that waits on an account
lock held by the calling job: InternetMusic's existing guarded transaction must
receive an already-admitted lease or use the same unit of work.

## Queue and lease lifecycle

An immutable operation identity binds account, device, kind and stable logical
operation ID. Reusing it with a different purpose is a conflict. A TRANSFER also
has an immutable actual target: audio_variant_id for download, server
upload_session_id for upload, or provider acquisition/attempt ID. The local
intent ID is idempotency identity, not permission to change files. First bind
checks ownership; each byte request checks exact target equality. An upload
session's metadata may be created before admission; bytes wait for the grant.
A PLAYBACK has a stable player-instance identity and
at most two revisioned current/next resource attachments. Replacing an attachment
retains the outgoing resource only until its I/O permits drain; a third live
resource is refused. Every attached recording is authorized to the same account.
States are WAITING, ACTIVE, RELEASED and EXPIRED. ACTIVE carries a monotonically
increasing fencing generation and a bounded lease expiry; WAITING carries a
bounded liveness deadline. Clients persist the operation before acquisition.
Waiting is a normal outcome with a bounded retry delay, never a job failure that
exhausts retry attempts. A cancelled job removes its waiting intent explicitly.

Admission cleans expired entries and selects eligible accounts fairly, then the
oldest waiting operation within each account. Selection is based on persisted
last grant order, so another API/worker process sees the same queue. Playback
has priority in scheduling; background grants rotate across accounts. A dead
client's unclaimed reservation expires instead of blocking the queue forever.
Both account and global capacity are checked in the same transaction as grant.

Repeated acquire returns the current lease without incrementing usage or renewing
its lifetime. Explicit renew/release must present the current fencing generation.
A stale heartbeat or release cannot resurrect or cancel a successor lease.
An expired operation may acquire again with a new generation, fresh random
activation UUID and the latest limits. RELEASED is terminal after completion or
cancel. Renew/release/permit calls identify activation UUID as well as generation;
cleanup cannot make an old request valid for a later recreation of an operation.
Completed/cancelled operations retain bounded replay evidence for seven days.

New acquires always join the existing queue. Each resource has independent global
capacity, so transfer grants cannot consume playback capacity or be starved by
playback arrivals. Choose the oldest eligible operation per account, skipping a
busy device, then least-recently granted account (ties by oldest enqueue and UUID).
Persist the grant sequence in the same transaction. A maximum of twenty live
WAITING operations per account/kind bounds storage and queue scanning.
Queue age uses a separate enqueued_at set on each new WAITING activation cycle;
reacquiring an expired operation cannot reuse its historical priority. Polling
or replaying an already waiting acquire does not reset its queue position.
Acquire/poll refreshes WAITING liveness for 45 seconds; advertised retry is
5 seconds with jitter, rising to 15 on contention. A grant has a 15-second claim
deadline; first renew or I/O open claims it. Claimed ACTIVE leases last 30 seconds
and renew every 10 seconds. Acquire, renew, release and policy increase drive a
bounded queue pass; a worker sweep at most every 5 seconds handles idle expiry.

The HTTP contract conveys purpose, logical operation ID and lease generation
alongside ordinary session authentication; a lease ID alone is not a bearer.
It never grants access to another account's recording or ownership reference.
HEAD and range validation do not create an additional logical lease. Active
upstream requests per lease are bounded for current/next overlap and reconnect;
I/O permits have their own random ID, activation fence and 5-second deadline,
renewable only while authority and parent lease remain valid. Per playback lease
at most three concurrent HTTP permits are allowed for Range reconnect/overlap;
per transfer at most one. Release/expiry begins draining: capacity remains charged
until all permits expire or close AND every registered process execution has
confirmed exit. Before filesystem/provider work starts, its execution registration
persists the exact permit/fence, actual target and owner-run identity. Every network
adapter checks cancellation at most every second, stops application HTTP progress
at the permit deadline and requests process-tree termination. Loss of PostgreSQL
prevents renewal and fails closed at that deadline. A blocked OS/NAS process can
outlive cancellation; its activation remains charged until exit is confirmed and
durably acknowledged. Heartbeat/lease expiry and a kill request never prove exit.
Unconfirmed registrations after a crash require verified supervisor/operator
reconciliation. They block reacquisition, conflicting attachment reuse and cleanup.
See the [approved stop amendment](explorations/AutPlay_Resource_IO_Stop_Amendment_v1.md).
The internal exit-acknowledgement seam distinguishes CLOSED (retained evidence)
from ABSENT (both the execution and permit accounting rows are already absent).
ABSENT is never stored as a lifecycle state, proves no historical process exit,
and grants no permission to start or resume. A trusted adapter must retain its own
verified death proof; this projection resolves a confirmation response lost just
before cleanup removes the closed receipt.
Renewals are owned by the playback service/download worker, not an Activity.

Uploads validate admission before reading PATCH bodies, and again before commit.
Downloads validate before first upstream read. Provider jobs acquire before
provider.download; quota waiting preserves the durable job/intent. Complete and
cancel release capacity after executions have confirmed exit; disconnect bounds
I/O authority even without release, while an unresolved execution retains capacity.
Release transfer capacity once executions close, bytes are transferred and ingest is queued;
InternetMusic's later COMMITTED/REUSED wait does not occupy a network slot.

## Admin authorization and receipts

Only the active bootstrap OWNER can edit server defaults/global limits, their
own account, or accounts whose provisioning link was issued by that OWNER.
ADMIN alone receives no new cross-account authority. Existing M6 web session,
exact Origin/CSRF and terminal receipt rules apply. Every mutation supplies an
operation ID, exact request digest, expected global revision and (for override)
expected account revision. A receipt and an audit event commit with the change.
An exact replay returns the prior result; a conflicting operation ID or stale
form cannot overwrite a newer policy. Replay does not bypass current authority.

The quota transaction revalidates the exact WebSession generation, token age,
revocation, idle/absolute expiry and bootstrap OWNER scope after taking identity,
admission and sorted account locks. HTTP authenticate/CSRF alone are insufficient
because their transactions have already ended. A helper must accept the already
locked identity; it must not acquire identity after admission or open a nested
quota transaction while an outer Web gate holds the account lock.

The same post-authentication race protection applies to existing authority
writers. PA2 commands reload the active OWNER's device and application session
inside their transaction, even for an operation replay. M5 invitation creation
and new lifecycle commands recheck the current account/device/session after
the admission lock; exact existing terminal lifecycle receipts remain retryable.
S1 browser review, decision and trust commands lock identity before admission,
then recheck the exact WebActor session generation and expiry before replay or
mutation. Generic application logout-all/device revoke similarly revalidate
the actor while holding the account/device locks. Trusted local recovery and
device proof bootstrap keep their explicit separate authority paths.

Quota policy receipts retain seven days of exact replay evidence. The bounded
admission sweep removes older quota receipts independently of the existing M6
terminal-receipt expiry policy; no audit events are removed by that sweep.

The form shows effective limits, inheritance, usage, queued counts and the impact
of lowering values; no foreign titles or listening history are disclosed.
Saving a form means the resource gates enforce that policy immediately.

## Wire operations and rollout

All documents are bounded flat JSON objects under `resource-admission/v1` with
`contract_version="v1"`, integer `schema_version=1`, canonical UUID identifiers
and UTC timestamps. Duplicate/unknown fields and stringified numbers are refused.
Client API is authenticated at `/api/v1/account/resource-admissions`:

- POST acquire: `operation_id`, `kind` (PLAYBACK/TRANSFER), `resource_type` and
  `resource_id` (PLAY_INSTANCE, DOWNLOAD_INTENT or UPLOAD_INTENT), with mandatory
  `target_id` for transfers (audio_variant_id or upload_session_id respectively).
  Provider acquisition admission is an internal worker operation.
  Owner/device/family come from the authenticated actor. The immutable acquisition
  digest covers those identifiers; no client-supplied account can select authority.
- GET `/{operation_id}` refreshes WAITING liveness and runs bounded admission.
  It reads ACTIVE without extending expiry. POST `/{operation_id}/renew` carries
  `activation_id` and `generation` and claims/renews an active grant.
- POST `/{operation_id}/release` carries `activation_id`, `generation` and
  `reason` (COMPLETE/CANCEL). A WAITING cancellation instead supplies the immutable
  operation digest because no activation exists. Release is idempotent within
  the exact activation; an obsolete activation receives a stale outcome.
- PLAYBACK attachment update carries `activation_id`, `generation`,
  `expected_attachment_revision`, `current_recording_id` and nullable
  `next_recording_id`. Its revision protects stale prebuffer/seek commands.

Status contains operation ID, kind, state, effective/account/global usage,
`retry_after_seconds`, nullable activation ID/generation and lease expiry.
WAITING reason is ACCOUNT_CAPACITY, DEVICE_CAPACITY or SERVER_CAPACITY;
authentication/authority errors do not disclose another account's usage.
HTTP 200 represents a stable WAITING or ACTIVE result, 409 immutable operation
conflict/stale activation, 429 bounded pending/rate pressure with Retry-After,
401/403 inactive authority, 503 unavailable/unconfigured admission.
Responses are no-store. Secrets, bearer tokens and music titles are absent.

The schema set is `contracts/resource-admission/v1`; the matching OpenAPI is
`contracts/openapi/v1/autplay-resource-admission.openapi.json`. Request documents
are at most 4096 UTF-8 bytes. Numeric fields require integer JSON tokens;
`1.0`, `1e0`, strings and booleans are rejected even where JSON Schema's
mathematical integer semantics would accept a floating representation.
The status includes `operation_sha256` for exact WAITING cancellation. It is
returned only after current owner authority checks and does not grant access.
WAITING responses have null activation, generation, claim and lease deadlines,
including reacquisition while the old activation's permits drain. If cancellation
races with promotion, the client polls after 409 and releases the returned active
fence. Error responses use the shared API `error` envelope.

In enforcing mode all server-audio GET and upload PATCH requests require valid
purpose/operation/activation/generation headers. Older clients receive a stable
`resource_admission_required` response and must upgrade; there is no legacy bypass.
Authenticated HEAD/invalid Range may return metadata/416 without an I/O permit.
Changing operation purpose or presenting a PLAYBACK lease for upload is refused.

Byte routes use exactly one value for each of `AutPlay-Resource-Type`,
`AutPlay-Operation-ID`, `AutPlay-Activation-ID` and `AutPlay-Generation`.
The resource type is PLAY_INSTANCE or DOWNLOAD_INTENT for a stream GET and
UPLOAD_INTENT for an upload PATCH. UUIDs use canonical lowercase hyphenated form;
generation is canonical positive ASCII decimal at most 2^53-1. Duplicate,
partial or malformed headers receive 400 `resource_request_invalid`. All four
absent receive 409 `resource_admission_required`; a valid but incompatible
purpose receives 409 `resource_purpose_mismatch`.

The URL supplies the actual audio_variant_id/upload_session_id. Playback resolves
the authorized variant to its recording inside the admission transaction and
then checks current/next attachment membership. It does not trust a client
recording hint. Renewals revalidate the same actual URL target and purpose.

The runtime middleware leaves upload PATCH bodies unread until the route's
authentication and admission gates finish. It enforces the 1 MiB chunk and
1024-frame bounds before forwarding each received frame, including false or
absent Content-Length. Admission JSON receives an early 4096-byte bound.

Upload commit revalidation runs after all filesystem work, in the caller's
existing upload transaction. It takes only SHARE NOWAIT row locks with bounded
statement/table-lock waits, never a nested admission transaction or advisory
lock. It checks current account/device/session/fence/permit, the OPEN owned
device upload, and a live library/ref/recording authorization path. Catalog
redirect absence is checked after the Recording SHARE lock. The caller then
immediately commits or rolls back; no later filesystem work is permitted.

## Required proof before enablement

- Separate processes cannot overshoot account or global capacity; races include
  device creation through different protocols, policy edits and authority revoke.
- Inheritance/reset/CAS/replay, rollback on audit failure, lower while active,
  raise without restart, expiry, stale fencing and fair cross-account waiting.
- Range/seek/prebuffer share playback identity; local/cache-only playback uses no
  server slot; download cannot silently use the playback factory.
- Simultaneous phone upload/download, Internet acquisition and A1 acquisition
  share the transfer limit and survive queue wait without duplicate ingest.
- Upload rejects before body admission; nested InternetMusic/Vault transactions
  do not self-block; authority revocation stops existing upstream I/O.
- Load evidence records CPU, disk, network, DB latency and queue depth on the
  target server. Synthetic tests alone do not establish production ceilings.

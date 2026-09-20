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
Trusted local initialization follows the bounded
[measurement report v1](AutPlay_Resource_Measurement_Report_v1.md). The operator
reviews the exact report/environment digests and simultaneous successful workload
coverage. Policy and audit receipt commit atomically; exact replay cannot overwrite
later edits. Structural report validation does not certify physical capacity.
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

LOCAL_BRIDGE authority is narrower still. The privileged local acquisition bridge derives it
only from the deterministic owner device created by the explicit provisioning command and its
durable `acquisition_bridge.enabled` audit grant. It has no user session, bearer credential or
job fence, and the schema permits it only for `UPLOAD_INTENT`. Device revocation, owner loss,
account suspension/deletion or authority-generation change invalidates it. Each chunk still uses
the common transfer queue, a retained `VAULT_UPLOAD` child, exact exit acknowledgement and the
post-write commit guard. Public HTTP routes cannot construct or select this authority.

Migration `0037_acquisition_authority` records the Internet source session family,
mode and account generation, and the A1 attempt account generation, at enqueue.
These fields are immutable. Historical NULL snapshots are not backfilled from
current credentials and cannot acquire worker I/O authority. Admission is locked
before enqueue operation/job/candidate locks. Internet enqueue reads fresh session
authority without FOR UPDATE before taking the owner sync lock, preserving the
concurrent publisher's account foreign-key lock compatibility.

An internal AcquisitionClaim contains only the real job fence, acquisition kind
and acquisition/attempt ID. The repository derives all authority from durable
rows and verifies the job type, payload, current claim and original lineage.
Its logical operation UUID is deterministically derived from kind and target;
the immutable digest excludes only changing worker ID and job attempt number.
Rebinding a live activation invalidates its previous execution authority and
waits for all prior permits/executions to drain. A still-live waiting cycle keeps
its queue age even if an intervening scheduler invalidated the old job fence.
Device waiting liveness expiry and previously active cycles receive a new queue age.

Migration `0038_worker_resource_wait` keeps worker WAITING demand until its original
authority/source or durable job lifecycle becomes invalid; waiting_until is NULL for
workers and remains bounded for devices. CPU job claims may yield as RESOURCE_WAIT
without spending a retry. The attempt fence stays monotonic; retry budget and backoff
use attempt_count minus resource_wait_count. Atomic defer checks a live grant again,
returns RECHECK for an expired exact grant, preserves checkpoints and honors cancel.

The resource fair order includes dormant quota waiters. Its winner receives a job
wake hint with a fixed 15 s grace, without an I/O activation. Job claim prioritizes
that hint, and only an authenticated real claim rebind may acquire the activation.
Between claim and rebind the same live job remains in the fair order. Expired wake
grace lets other operations proceed if the consumer is unavailable; the waiter's
original queue age and claim hint remain. A bounded validity sweep advances past
valid rows without changing their queue age. Genuine retry backoff is preserved.

Worker authentication is distinct from byte-target eligibility: exact release
is possible after an Internet PROCESSING or A1 INGESTING handoff. New provider
I/O is forbidden after handoff; A1 also rejects any existing provider upload
bound to that acquisition attempt. A1 acquisition, ingest and publication transactions
also require the captured account generation; denied publication preserves the
source_authorization_unavailable terminal reason. These internal seams do not
themselves enable provider handlers or provider byte execution.

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

The Linux retained-tree backend owns one domain cgroup v2 beneath an explicitly
delegated root. It never mounts cgroupfs or changes delegation/controllers. The fixed
launcher cannot fork before attach/GO; descendants inherit its cgroup even after
starting a new session. Stop latches before cgroup.kill, and exit evidence requires
both the exact root's wait result and fresh populated=0 for the retained cgroup.
The supervisor retains that cgroup through durable exit acknowledgement. The
numeric attachment is serialized with the pinned CPython Popen reaper; an exited
unreaped PID cannot be reused during the write. A busy reaper refuses attachment.
This contains trusted tools which do not self-migrate or use external brokers;
it is not a hostile same-UID sandbox. Missing delegation/control access or uncertain
identity/observation fails closed. This backend's isolated proof does not activate
production workers or complete internal ingest ownership/admission; ingest and
analysis continue to be separate from audio TRANSFER leases.

Provider staging ownership is durable independently of those accounting rows. A new
PROVIDER execution registers its exact owner, acquisition, job fence, activation,
permit, owner run and generated staging key before child GO. Exit confirmation copies
the exact closure evidence in the same transaction before accounting cleanup can
remove it. Historical execution rows without this ownership receipt may drain but
cannot authorize a new provider writer. Reconciliation cannot treat a registered
pre-handoff file as an orphan merely because its execution closed or its lease expired.
Sealing records verified digest/size after confirmed exit. Handoff must atomically
bind that receipt, the single server upload and its ingest job. Explicit cleanup must
claim ownership before filesystem work and exclude a concurrent handoff.
After a successful HANDED_OFF receipt, scratch retirement has its own immutable
claim, separate from abandoned staging cleanup. It can move only that execution's
provider workspace; the upload's staging file, quarantine and CAS remain outside
the claim. Claim and completion use separate transactions, with filesystem work
after the claim commits. Replay must preserve the same claim, and absence of both
the workspace and its retired destination cannot count as successful retirement.
Retirement preserves bytes in provider-retired; deletion requires a separate
retention path. Provider maintenance uses one durable slot for the configured Vault,
separate from account transfer quotas. PREPARED commits before spawn; exact HELLO
identity and RUNNING commit before GO. The slot remains occupied until retained
process exit and durable acknowledgement. Caller timeout, interrupted waiting,
connection loss and parent restart cannot release it. The fixed maintenance child
has no database credentials and starts no descendants. Production scheduling and
general Vault reconciliation still need their remaining composition and ownership
work; the provider maintenance adapter alone does not activate those paths.

The incremental inventory scanner uses that same singleton reservation, including
while waiting between bounded pages. Its independent stop supervisor can request
termination during a blocked database/pipe call; the retained owner releases the
reservation only after exact process exit and durable acknowledgement. An exhausted
inventory page is neither exit proof nor proof that unobserved bytes are missing.
Scanning and retirement must be composed sequentially; the scanner does not grant
another maintenance process capacity while retaining its live iterator. The local
CLI retains that iterator across bounded pages, then acknowledges exact exit before
draining durable orphan claims. Its page budget is not a total invocation cutoff;
restart drains saved canonical claims and scans afresh. Drain-only never opens a
scanner. No dead scandir iterator is advertised as a resumable snapshot.
Orphan CAS retirement first commits a claim for an exact lowercase SHA-256 storage
key under admission and the same digest lock used by publication preparation.
Any VaultObject with that digest, in any state, or local filesystem replica with
that key excludes orphan retirement. A terminal job, expired upload or missing
replica does not remove the protection of an existing object row. Publication
preparation rejects an active orphan claim. The maintenance execution binds the
claim and key, preserves bytes under a claim-specific quarantine destination,
and completes the claim only after acknowledged successful exact child exit.
Another unfinished maintenance execution for that claim prevents completion.
A delayed caller cannot launch against a completed claim or retire a later
publication of the same digest. This mechanism covers unregistered CAS only;
the legacy destructive DB reconciliation path has been removed. CLI reports
explicitly retain the tracked-reconciliation gap. Tracked CAS and staging still
need their own ownership proof before generic integrity repair or cleanup.
If an orphan candidate disappears before its new claim, retirement still fails
when both source and that claim's quarantine destination are missing. A separate
`ORPHAN_MISSING` check-only process may resolve the claim after confirming both
exact paths absent within the same safe directory identities, followed by exact
zero exit and durable acknowledgement. Its completed outcome is `MISSING`, derived
from the immutable maintenance action; it is never counted as `RETIRED` and proves
no historical writer exit. Present bytes, unsafe/changed directories, or lookup
errors leave ownership active. Replaying that completed claim cannot inspect or
mutate a later publication. Callers retain the canonical claim ID before work.
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

Upload session creation/replay persists metadata only. Its committed row owns the
opaque staging key before any file can appear. The first new-byte PATCH creates
staging inside its registered RUNNING VAULT_UPLOAD process under the upload row
lock. A missing file is creatable only when the durable received size is zero;
a missing acknowledged prefix is a storage failure, never a reset to zero. A retry
may truncate only the uncommitted suffix. Files and their newly initialized
directory entries must be synced before acknowledging the chunk on Linux.

The admitted child checks the free-space watermark against the complete remaining
expected upload size before every new-byte write. This is a conservative snapshot
check, not a reservation against concurrent filesystem consumers. Low space returns
retryable 507 `vault_capacity_low` without a new receipt or staging file mutation.
POST/replay, duplicate chunk receipt replay and ordinary non-expired completion do
not perform filesystem capacity probes. Completion may enqueue metadata; the
ingest worker still checks its own watermark before starting.

Device upload cancellation and expiry commit a canonical cleanup claim with the
terminal transition. HTTP performs no filesystem cleanup. Only CANCELLED/EXPIRED
device uploads without ingest/CAS/source bindings are eligible. OPEN can expire;
OPEN/SEALED can cancel; PROCESSING cannot return to either cleanup state. Actor
identity is immutable, and locked reads refresh any earlier ORM snapshot. A
prepared or otherwise unclosed upload execution prevents maintenance even after
the terminal intent commits. Job state and lease expiry never prove writer exit.

`vault-upload-cleanup` drains pending claims and backfills eligible historical
terminal uploads. It uses the shared durable maintenance singleton, a retained
child and an exact successful PROCESS_EXIT acknowledgement before completion.
The global admission lock precedes upload/claim locks in maintenance; the HTTP
terminal transaction takes upload then claim and never nests the global lock.
The key and claim are immutable. Present bytes move to claim-specific quarantine
without replacing an existing different file. Missing root/staging/quarantine
directories defer work; the cleaner never creates a possibly unmounted namespace.
Both leaves absent within stable safe directories may complete cleanup without
asserting file retirement, integrity or the history of those bytes. Each metadata
page is bounded to 1..100 entries and keyset progress prevents a failed claim from
starving later claims within that pass. No scheduling is enabled by this CLI.

Internal ingest ownership uses a separate immutable execution receipt bound to the
upload, staging key, owner run and exact job attempt. PREPARED commits with the
PROCESSING transition before any registered child is spawned. One unfinished
receipt excludes every successor for that upload/staging key; job recovery,
deadline expiry, terminal upload state and process-stop requests cannot release it.
RUNNING binds the retained child identity. Start/replay/renew recheck the job and
source authority; authorization lasts at most five seconds and never beyond the
job lease. Python checks and the SQL update guard both reject renewal after the
old deadline. Exit confirmation remains possible after authority or lease loss.

Every ingest metadata boundary requires the expected RUNNING receipt and child
when one is supplied; absence cannot fall back to legacy behavior. Legacy calls
without an expectation reject any unclosed receipt, including before terminal
replay. Upload input/owner/job/source bindings cannot be rewritten once registered.
The retained-process adapter requires explicit tree containment and a fixed launcher
for this internal ticket, without constructing an audio TRANSFER permit.

The contained WORK coordinator enforces the actual server grant duration from
the monotonic RPC-start time. Its independent watchdog stops the tree even when
renewal or the single metadata/pipe owner blocks. Terminal metadata first freezes
and settles renewal under the remaining grant. The whole callback must settle,
the exact root/tree must exit, and durable acknowledgement must succeed before
local ownership is forgotten. Uncertain prepare absence is reconciled under the
same admission lock, not inferred from an unlocked missing-row read.

Capacity, hashing, media tools and CAS publication run in the fixed child. WORK
retains staging after publication. Existing namespace validation rejects missing
mounts and shard links before writes; publication syncs the entire CAS directory
chain. Registered finalization atomically creates an immutable cleanup claim
bound to the exact WORK execution, upload, staging key and published CAS result.
Historical terminal metadata alone cannot establish this claim. Cleanup waits
for acknowledged PROCESS_EXIT of that WORK tree (a nonzero WORK exit is allowed
after finalization), then uses its own retained execution and five-second grant.
It verifies and syncs matching CAS bytes before removing and syncing staging.
Completion requires exact acknowledged zero exit of the cleanup tree and no other
unclosed writer; a completed replay never launches a child. Job expiry or source
revocation after finalization cannot revoke this canonical cleanup authority.
SQL guards freeze the claim/result and exclude cleanup and upload/WORK writers
in both directions. Durable RESOURCE_WAIT preserves retry budget when an older
upload, WORK or cleanup execution still owns the target.

WORK and cleanup have separate start/renew authority. Migration
`0049_internal_io_budget` adds a separate reviewed global capacity shared by their
unclosed receipts and provider-maintenance receipts. Count plus PREPARED insertion
is atomic under the common admission lock and repeated by SQL insert guards.
The initial policy is unconfigured. Lowering capacity retains all existing charges
and permits their otherwise valid renewal/closure; only new admission waits.
Its joint measurement report must cover the configured audio ceilings and the
worst permitted internal mix; see the measurement report contract.

`worker_cpu.py` connects contained WORK, canonical cleanup and controlled Jamendo
acquisition. Periodic and --once work both observe stop signals independently of
blocked retained waits; a shared five-second shutdown budget keeps signal handlers
installed through exact-exit/DB-ack draining. Missing cleanup bytes leave a pending
claim while keyset traversal and job polling continue. The Jamendo child intersects
the configured provider download limit with the Vault limit.

WORK exit zero alone does not prove staging cleanup. Activation must drain
unregistered legacy workers; a new receipt cannot retroactively discover their
filesystem work. Production delegation, complete remaining path integration and
real joint measurements remain activation prerequisites.

### Contained metadata enrichment

Migration `0050_metadata_execution` adds retained metadata execution and provider
pacing rows. Every unclosed metadata receipt charges the same internal capacity;
new metadata work requires joint-report version 2 including METADATA_ENRICHMENT.
A v1 report remains valid for its earlier paths and cannot activate this worker.
A policy cannot downgrade from v2 to v1 while retaining a metadata-inclusive runtime.

The worker pins account authority generation, exact job attempt, active owned
library ref, metadata generation and, when available, the canonical audio tuple.
A metadata-only snapshot never acquires audio later during that execution. Start,
renew and publication validate those facts under admission/owner/job/ref locks.
REFRESH/SELECT replace metadata generation and revoke old work. Ordinary EDIT
retains generation; locked manual fields still win over background merges.
Publication flushes all result/history/sync writes before a final SQL receipt
check without extending the grant; an expired check rolls the transaction back.

The fixed child performs Vault verification, direct immutable CAS reads, ffprobe,
fpcalc, HTTP and pipe-only artwork normalization. No full temporary audio copy or
artwork scratch file is created. Application database credentials never enter the
child. An explicitly configured provider proxy may enter its private environment;
the optional AcoustID key travels only in the private POST body, never arguments,
URLs, results or routine logs.

Provider pacing reserves one durable in-flight request through short transactions.
The row stays occupied until that request has finished or the exact process tree
has exited and closure is acknowledged. The next request waits a further 1.1 seconds.
No database transaction spans the provider call. An expired execution cannot release
an occupied request merely by timing out. Retained callback/drain and signal handling
follow the WORK lifecycle, including --once and independent control-pool disposal.

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

# Resource control and worker authority evidence, 2026-09-16

Scope: local development on the current Admin/accounts working tree. No production
migration, deployment, measured-capacity initialization, commit or push was performed.

## Dedicated control pool

The optional API/stream ResourceIoRuntime owns a separate four-connection PostgreSQL
pool, without overflow, with 250 ms checkout, 1 s statement, 500 ms lock and 2 s connect
bounds. Its pool remains alive after HTTP shutdown while retained worker transactions
or exact process-exit acknowledgements remain unsettled. Eventual coordinator drainage
disposes the pool once. Startup is serialized; duplicate/concurrent startup cannot
dispose a live instance.

Actual PostgreSQL tests cover exhausted data-pool isolation, control-pool exhaustion,
server-enforced query/lock timeouts, retained upload row locks and charged capacity,
eventual disposal without a second shutdown, and concurrent startup.

## Enqueue lineage and worker admission

Migration 0037 adds four nullable, immutable authority columns. New Internet selections
capture the authenticated original family/mode/generation. Manual and automatic A1
attempts capture the account generation under the shared admission lock. Existing NULL
snapshots remain unbound. Downgrade refuses to discard newly captured authority.

The internal worker seam derives authority from the exact job payload/fence and saved
intent. Rebind preserves logical operation/digest, rejects stale workers and retains
capacity until prior executions close. A still-live waiting cycle preserves queue age
across an intervening scheduler pass. New provider I/O stops at the ingest handoff;
exact release can free capacity immediately after that handoff.

Readonly review found and closed the concurrent-start disposal race, Internet enqueue
sync/FK lock cycle, A1 policy/job lock ordering, cleanup after PROCESSING, A1 byte work
after handoff, and scheduler/rebind queue-age loss.

## A1 publication authority

Acquisition, ingest handoff, analysis and Vault publication transactions recheck the
original account generation under admission-first locking. Recovery after staging
quarantines the upload before start, prepare or final materialization. Filesystem and
media work remain outside these transactions. A denied prepare returns a distinct
SOURCE_UNAVAILABLE result so the handler preserves the source-revocation reason in
the upload, candidate and attempt instead of overwriting it as an integrity conflict.

## Durable resource waiting

Migration 0038 separates RESOURCE_WAIT attempts from real failures. The job attempt
number remains monotonic for fencing; retry limits and backoff subtract the recorded
resource wait count. A short admission-first transaction rechecks the exact admission
before releasing the CPU claim. A live grant wins that race, an expired grant requests
re-admission, and cancellation wins over deferral. The worker does not finish an
already deferred attempt a second time.

Worker demand has no device heartbeat TTL. Its saved account/session/source lineage
and nonterminal job lifecycle remain required. Bounded cleanup advances past valid
waiters while preserving enqueued_at. A dormant winner in the resource fair order
wakes its job ahead of new ordinary work; it receives no activation before its real
claim rebinds. The fixed 15 s wake grace never extends on repeated sweeps. An absent
consumer stops blocking other grants after grace, retaining queue age and its wake
hint. Real retry backoff and paused jobs are not accelerated.

Review closed expired-grant handling and dormant-waiter overtaking, including the
claim-to-rebind window and unavailable-consumer liveness. Provider handler integration
remains separate from these internal scheduling seams.

## Verification

The typed Internet repository now returns an existing handoff before any provider
work. Identity preparation joins its transaction using owner UUIDs. New handoff
requires the original account/session authority, current source-job fence, live
owned identity and a matching durable exit receipt; any unclosed provider execution
for the acquisition prevents handoff. Seal, one INTERNET upload, one ingest job,
source PROCESSING and durable HANDED_OFF commit atomically. Digest/size/target replay
conflicts fail closed, and the source lease is checked after all writes. The handler
and provider byte coordinator still need integration with this repository.

Migration 0040 registers durable provider staging ownership atomically with a new
PROVIDER execution, before child GO. Exact closure proof is copied into that record
in the exit-confirmation transaction, so close, sweep and activation replacement
can remove ephemeral accounting without losing file ownership. Historical executions
without the new receipt can drain but cannot start/replay a provider writer.
Reconciliation protects the registered staging key while ownership, exit, sealing
or an explicit cleanup claim remains pending. The schema reserves atomic seal,
handoff and cleanup transitions; their application methods are the next integration
step. No provider handler is activated by this foundation.

Internet publication now rechecks the original generation, device/session family,
source job identity and owned live Recording/ref/library projection at each ingest
boundary. Recording/ref/entry locks also serialize catalog redirects and library
removal. Completed upload replay keeps its ingest fence but never restores a removed
entry or reauthorizes the already committed historical receipt.

Vault finalization, canonical selection, library/sync projection and Internet READY
commit in one transaction. The owner resolver checks the actual selected canonical
variant, which may differ from the newly ingested one. Nonblocking canonical/object/
replica locks turn contention into a rolled-back retry. Fresh current-object status
checks prevent finalization from reviving bytes already rejected by reconciliation.
Failure-only reconciliation acquires an exact source row without waiting; a busy
source skips the whole repair, including staging mutation, and remains in the report.

Migration 0039 adds an explicit INTERNET upload actor with no device/A1 actor fields,
a unique source-acquisition foreign key and immutable upload/selection lineage. A
provider transfer cannot reopen after either side of its ingest binding is saved.
Device create/replay also requires the original device and DEVICE actor. The named
cyclic foreign key uses separate ALTER metadata so Alembic can compare both tables.
This is a schema foundation; no handler creates this new actor yet.

Ingest start, prepare, finalize and quarantine carry the exact worker fence into
their short transactions. Admission and job locks precede upload/CAS locks. A fresh
fence/deadline check runs again after flush, so expiry while waiting on a later lock
rolls back the whole transition. Replaced workers cannot quarantine a successor.
The identity preparation seam now joins its caller's transaction without a fabricated
session identity or nested unit of work.

- Initial control/upload/coordinator/stream batch: 18 passed.
- Enqueue lineage, migration metadata, A1, Internet and control batch: 51 passed.
- Full migration/close-gate, execution, A1 automation and enqueue-regression batch:
  66 passed (84.72 s), including the real concurrent search/selection regression.
- Worker/device/lineage batch: 23 passed; final worker/device regression batch:
  20 passed (27.98 s).
- Strict mypy passed for 15 affected source/test files; scoped Ruff checks passed.
- Worker/A1 adjacent regression batch: 38 passed (34.20 s).
- A1 publication, Vault runtime and handler batch: 18 passed (13.84 s), including
  actual PostgreSQL/filesystem recovery at three repository boundaries and recovery
  after media work through the real handler and transactional adapter.
- Publication changes passed strict mypy/Ruff for five affected files. Bounded
  readonly review closed the handler error-overwrite finding with no further findings.
- Durable-wait, worker-admission and metadata batch: 29 passed (31.34 s), including
  13 new wait scenarios. These cover repeated waits without retry exhaustion, CPU
  yielding, cancellation, source revocation, fair wakeup, fixed grace and retained
  old execution charge through real successor job claims.
- Final migration/schema, job runtime/concurrency, device admission and policy/view
  batch: 66 passed (79.43 s). Live Alembic metadata drift check passed separately.
- Strict mypy passed for 21 affected files; scoped Ruff passed after formatting and
  a test SQL literal split. Full adjacent-migration round trip passed (22.25 s).
- Ingest fence and adjacent A1/Vault PostgreSQL batch: 21 passed (29.61 s), including
  13 new cases for recovery/reclaim, cancellation and identity checks, and four real
  upload-lock waits crossing the lease deadline. Ten handler/service tests passed.
  A mixed directory invocation lost fixture discovery for eight later tests;
  the complete PostgreSQL group passed together on the corrected invocation.
- Identity preparation: three existing music-library tests and the new single-pool
  transaction rollback test passed. Strict mypy passed for six affected source/test
  files, scoped Ruff passed, and readonly review closed the lease-expiry finding.
- Internet-lineage initial schema/worker/library batch: 20 passed (18.73 s).
  Final two lineage/replay regressions passed (15.18 s); adjacent upload and ingest
  fence tests passed (20 cases). Full migration/schema batch passed (27 cases,
  114.47 s). The two live Alembic checks then passed with SQLAlchemy warnings promoted
  to errors after explicit cyclic-FK metadata; six metadata checks passed separately.
  Strict mypy passed for eleven affected files and scoped Ruff passed.
- Internet authority helper: 17 passed (33.65 s), including four actual catalog/library
  lock races. Initial atomic publication and adjacent batch: 29 passed (53.20 s).
  Terminal replay and prepare-retry revocation fixes: six passed (22.56 s).
- Canonical contention and source-repair batch: 21 passed (49.32 s). Final expanded
  batch passed 29 cases; its corruption fixture incorrectly made a Windows file
  read-only. The fixture now follows the existing platform-specific corruption test;
  both current-CAS reconciliation regressions passed (16.26 s). Strict mypy/Ruff
  passed for the affected runtime and race tests. Readonly review closed the final
  current-CAS revival finding.
- Provider staging and adjacent execution/publication/upload exclusion: 55 passed
  (151.43 s). Full migration/metadata lifecycle: 28 passed (100.08 s). Final staging,
  worker, metadata and live Alembic check: 26 passed (46.27 s), with SQLAlchemy
  warnings treated as errors by pytest. This final batch includes legacy start denial
  and permitted draining. Thirteen affected files passed strict mypy and scoped Ruff.
  Inventory: 148 tables, 1675 columns, 141 explicit indexes; head 0040_provider_staging.
- Typed handoff and adjacent identity/library: 14 passed (39.56 s). Expanded handoff,
  authority and staging batch: 37 passed (74.34 s), including the real ingest path
  to READY and one retained INTERNET_ACQUISITION transfer. Final handoff: 14 passed
  (47.59 s), including removed ref/entry and a real staging-lock wait crossing session
  expiry. Readonly review closed exception-classification findings; typed errors retain
  source_authorization_unavailable. Scoped strict mypy/Ruff passed.

Evidence uses disposable PostgreSQL 18.4 and synthetic actors/provider results. Worker
execution tests use synthetic process identities to verify database fencing; the real
process and HTTP evidence is in ADMIN_RESOURCE_HTTP_2026_09_16.md.

## Abandoned provider cleanup (2026-09-17)

The internal cleanup service first commits an exact deterministic cleanup claim under
admission/job/staging locks. Filesystem work then runs outside every database unit of
work. Completion is replayable after lost acknowledgements or database failures.
Only confirmed-exited files can be claimed; a live execution, any upload binding,
and a completed handoff protect the staging key. Lease expiry, RETRY_WAIT, PAUSED,
and transient provider/operator gates do not authorize cleanup. Original account,
session-family and A1 source/policy authority remain decisive. A1 source expiry uses
fresh PostgreSQL time after the source row lock; failure to read the clock rolls back.
The legacy A1 handoff rejects the reserved provider namespace until its own atomic
provider receipt transition is implemented.

The filesystem adapter preserves final staged bytes with hardlink/unlink and retires
the complete provider-work directory, including partial files and fragments. Replays
validate regular-file/directory identity without following symlinks or Windows reparse
points. Directory retirement uses an atomic no-replace primitive (Windows rename;
Linux renameat2 RENAME_NOREPLACE), with no cross-filesystem copy fallback. Conflicting
evidence is preserved. This is abandonment cleanup, not permanent deletion or a
backup retention implementation. Successful HANDED_OFF scratch retirement still needs
integration with the controlled provider coordinator.

Verification: 22 new PostgreSQL cleanup cases passed (50.74 s); three real cleanup
race/partial-binding tests plus Internet handoff and A1 publication passed (21 cases,
57.88 s). Windows filesystem batch: 16 passed, two symlink cases unavailable due to
host privileges. The same filesystem tests ran in isolated Linux Python 3.14.7 with
read-only source/dependency mounts and no network: all 18 passed, including symlinks
and the actual Linux no-replace syscall. Seven affected files passed strict mypy,
Ruff and formatting. No production activation, migration, commit or push was made.

A repeat of the database/FS race exposed Windows ACCESS_DENIED during a concurrent
unlink. The implementation now accepts an uncertain unlink/link only after observing
source absence and the original regular destination inode. Otherwise the durable claim
remains retryable. Deterministic before/after-unlink fault tests and ten bounded concurrent
filesystem cases cover this fix. Final proof: 25 PostgreSQL cleanup/race cases passed
(56.10 s) with SQLAlchemy warnings treated as errors; Windows 27 passed/two unavailable
symlink cases; Linux all 29 passed (3.04 s). Final strict typing and Ruff passed for all
seven affected files. Independent read-only review closed the clock, test-key and
concurrent-unlink findings, with no remaining concrete finding in this slice.

Relevant OS semantics: [Microsoft deletion lifecycle](https://devblogs.microsoft.com/oldnewthing/?p=106194),
[Linux no-replace rename](https://man7.org/linux/man-pages/man2/rename.2.html).

The disposable Docker engine stopped during the first PostgreSQL run; its 19 setup
errors were connection failures, not implementation evidence. After the user restarted
Docker, the same owned container was restarted and its new loopback mapping (1520)
was verified. Subsequent test URLs use connect_timeout=3. No other container was changed.

Implementation references: application/provider_cleanup.py,
adapters/postgresql/provider_cleanup.py, adapters/filesystem/provider_staging.py,
and the persistent-authority/legacy-namespace boundaries in discovery_runtime.py.

## Windows provider process containment (2026-09-17)

The retained-process adapter now requires an explicit process-tree backend and a
fixed launcher for PROVIDER executions. The new Windows backend owns an unnamed,
non-inheritable Job Object with KILL_ON_JOB_CLOSE and no breakaway flags. Attachment
borrows the exact CPython 3.14.7 Popen process HANDLE, validates its PID, and happens
before GO. It never reopens a PID. Stop, attach, empty-tree sealing and handle close
are serialized; stopping an empty Job forbids any later attachment.

Exit evidence requires completed spawn, the retained root's actual exit and a fresh
Job accounting query with ActiveProcesses equal to zero. An exited root with a live
descendant, a query failure, or uncertain Popen creation cannot produce evidence.
Termination still targets the Job after the root exits. The supervisor keeps the Job
through durable CLOSED/ABSENT reconciliation and pipe-owner completion; a failed
CloseHandle keeps registry ownership for retry. This contains the fixed launcher's
ordinary subprocesses; it is not a sandbox for hostile code or external broker work.
Existing Vault-only launches retain their single-process behavior.

Verification on Windows with Python 3.14.7:

- Final real process-tree and existing Vault child batch: 25 passed (3.32 s).
  Includes root-exited/descendant-live, stop during Popen/attach/factory work, failed
  assignment/query/termination, nested Jobs, exact handle identity, pre-spawn
  configuration failure, uncertain post-creation failure and retry after failed close.
- Three real PostgreSQL/provider-tree cases passed (16.39 s): release, actual device
  revocation and quota lowering all retain transfer charge while a descendant lives.
  After tree exit is acknowledged, another queued transfer can acquire the slot.
  The durable staging receipt preserves the same exit evidence through permit cleanup.
  SQLAlchemy warnings are errors. Nine adjacent PostgreSQL retained-process/upload
  cases also passed in the earlier combined run; no affected runtime change followed
  except portable ctypes typing.
- All seven affected Python files pass strict mypy for both Windows and Linux targets,
  Ruff and formatting. An isolated Linux container with read-only source and no network
  confirms that importing the module works and constructing WindowsJobTree refuses the
  unsupported platform before any native API binding. This is not Linux containment proof.

The initial PostgreSQL revoke fixture changed only account generation and bypassed
the existing logical-grant invalidation writer, so its immediate queue-promotion
expectation was invalid. It now invokes SqlAlchemyAuthRepository.revoke_device and
checks that renewal is refused. Production admission behavior was not changed to
accommodate the test. Test import/type errors were resolved without disabling checks:
the existing pytest prepend layout uses an absolute helper import; dynamic API fault
injection uses monkeypatch.setattr; platform-specific ctypes exports are explicitly
bound only after the OS gate. Alternatives reviewed were converting the tests into
a package or installing a helper package for importlib mode, and using a local Any
API facade or a full typed API protocol for fault injection. The chosen fixes keep
the existing test layout and limit dynamic typing to the Windows API boundary.

Implementation references: adapters/process_tree.py, adapters/windows_process_tree.py,
adapters/filesystem/vault_process.py; tests/test_windows_process_tree.py,
tests/postgresql/test_provider_process_tree.py and their bounded synthetic launcher.
Independent read-only review, including the final tests and portable-typing delta,
closed with no concrete finding. The reviewer did not run tests. No provider production
activation, migration, commit or push was made.

References: [Microsoft Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects),
[nested Jobs](https://learn.microsoft.com/en-us/windows/win32/procthread/nested-jobs),
[pytest import modes](https://docs.pytest.org/en/stable/explanation/pythonpath.html),
[pytest fault injection](https://docs.pytest.org/en/stable/how-to/monkeypatch.html),
[mypy dynamic boundaries](https://mypy.readthedocs.io/en/stable/dynamic_typing.html).

## Controlled provider execution and retry (2026-09-17)

The separate ControlledInternetAcquisitionHandler now acquires the stable worker
TRANSFER, yields durable RESOURCE_WAIT when capacity is unavailable, and executes
ProviderIoExecutor through VaultIoCoordinator. The coordinator retains the provider
tree, renews the five-second IO permission and the thirty-second operation lease,
and accepts a result only after natural whole-tree exit and durable acknowledgement.
The typed repository then binds exactly one verified staging file to one upload and
ingest job. Production worker composition is still unchanged.

Failed provider attempts no longer RELEASE their stable operation: RELEASED is
terminal and prevented the next job claim from rebinding. Release follows successful
handoff only. Expected database/transport/start failures become retryable job errors;
the launch grant gate remains denied even when Thread.start fails after OS creation.
No caller cancellation substitutes for the retained thread/process exit receipt.

Provider bytes use a fixed private launcher with no database/session environment.
The root creates a unique workspace only after GO. Its bounded pipe reader checks
each block against the byte limit BEFORE disk write. Source-to-staging copy checks
regular-file identity, size, links and mutation, hashes each bounded copy, and compares
an independent staging read/hash. Failed partials remain under durable cleanup ownership.

The fixed media helper selects exact downloader classes, never selector fallback:

- Progressive HTTP(S) AAC/Opus preserves the original container using HttpFD stdout.
  In-stream retries, Range headers, resume, fragment chunking and adaptive buffers
  are disabled. Whole-job retry creates a new owned workspace. This prevents a server
  ignoring Range from appending a complete retry to an already emitted prefix.
- HLS/DASH uses exact FFmpegFD stdout, with no native fragment-file fallback. AAC is
  copied to ADTS and Opus to Ogg. The configured FFmpeg is in the same retained tree.
  It must report the supported 8.1.2 version in a bounded preflight; the same resolved
  executable is then passed through ffmpeg_location. Progressive HTTP needs no FFmpeg.
  Unsupported formats/downloader requirements fail without creating fragment files.
- HTTP framing failures are rejected. This is not a claim to prove completeness of
  an arbitrary response delimited only by connection close, or the identity of music
  from its hash. Final media validation and original authority checks still apply.

Read-only review found an additional admission timing race: the timestamp obtained
before waiting on an exact JobRow lock could authorize an expired job/session. The
authority gate now reads clock_timestamp after that lock; application checks refresh
the activation/permit timestamp after authentication as well. This applies to open,
prepare, start, renewal and the other admission operations sharing that boundary.

Verification with current source, Python 3.14.7 and disposable PostgreSQL 18.4:

- 55 PostgreSQL cases passed in 122.43 s: controlled provider success, HTTP failure
  then real JobWorker retry, control/worker start failure then retry, a 32-second real
  loopback download spanning both leases, fifteen contended-job-lock expiry cases,
  admission/worker rebind and HTTP upload/stream/disconnect regressions. SQLAlchemy
  warnings were errors. The retry assertions prove the same operation ID, a fresh
  activation, one upload, exact exited staging receipts and empty local ownership.
- Sixteen existing coordinator/start-failure cases passed in the preceding combined
  PostgreSQL run; no runtime implementation change followed that successful run.
- Windows combined provider/filesystem/process/Vault batch: 78 passed, two symlink
  privilege skips, 21.56 s. The final expanded media suite separately passed all
  21 cases in 20.32 s. AAC, Opus, HLS and DASH cover success, overflow and truncated
  input; progressive HTTP also covers missing final zero chunk and short declared
  Content-Length, with exactly one request. Successful outputs pass real FFmpeg
  decode and Vault ffprobe inspection. No fragment scratch files are permitted.
  Progressive children run with an empty PATH, proving independence from FFmpeg;
  the incompatible-version case proves rejection before FFmpeg media execution.
- Linux isolated source/filesystem batch: 38 passed, one Windows-only integration
  skip, 3.16 s. This includes the two symlink cases unavailable on Windows. Pinned
  Linux FFmpeg 8.1.2 separately processed the synthetic DASH stream successfully.
  These are filesystem/media proofs, not Linux cgroup containment acceptance.
- All nineteen affected implementation/helper/test files passed strict mypy, Ruff
  and formatting; the final media preflight/framing delta also passed all three.

Runtime/setup findings were resolved without weakening assertions. Windows PATH had
FFmpeg 9.0.1: a complete synthetic DASH stream ended with a demux IO error under
-xerror. The same case succeeded with project-pinned 8.1.2 in Linux and Windows.
The Windows proof used an isolated temporary Gyan 8.1.2 essentials archive, verified
against release SHA-256
`db580001caa24ac104c8cb856cd113a87b0a443f7bdf47d8c12b1d740584a2ec`;
only the test process PATH changed. FFmpeg 9.0.1 is rejected by the provider preflight.

Repeated-error research compared explicit exact downloaders, forced CLI FFmpeg,
progress hooks and filesystem quotas for byte limits; only explicit stdout routing
and the bounded writer avoid uncontrolled fragment writes here. For HTTP/mux EOF
failures, options examined were persistent HTTP connections, streamable MP4 indexing,
preserving progressive bytes and changing the FFmpeg runtime. The final implementation
uses persistent FFmpeg HTTP, preserves progressive containers, and validates the
pinned runtime. Test media generation now has its own working directory, avoiding
Windows DASH segment output in the repository root; generated stray fixtures were
removed. Linux Python checks use isolated pinned Linux dependencies because Windows
native extension wheels cannot be mounted as a Linux runtime.

References: [FFmpeg protocols](https://ffmpeg.org/ffmpeg-protocols.html),
[FFmpeg formats](https://ffmpeg.org/ffmpeg-formats.html),
[FFmpeg error handling](https://ffmpeg.org/ffmpeg.html),
[FFmpeg downloads](https://ffmpeg.org/download.html),
[Gyan 8.1.2 release](https://github.com/GyanD/codexffmpeg/releases/tag/8.1.2),
[HTTP incomplete messages](https://www.rfc-editor.org/rfc/rfc9112.html#section-8).

Independent read-only review closed the retry/startup ownership, fresh-clock,
explicit downloader/framing and resolved-runtime findings after the final deltas.
The reviewer did not execute tests. No production activation, migration, commit,
push or deployment was performed; adjacent worktree changes were preserved.

## A1 authority lock waits (2026-09-17)

The A1 claim, pre-download and legacy ingest handoff now validate the exact locked
Job row against `clock_timestamp()` and cancellation. A cached Job cannot hide a
committed cancellation. Attempt/source/policy checks use database time after the
last lineage lock, including automatic policy waits; candidate, attempt, source
and policy rows are refreshed even when the Session already holds those objects.
Claim and ingest repeat authority checks after their final flush. Expiry during
identity creation therefore rolls back the identity, upload and ingest enqueue.

Nineteen new PostgreSQL cases exercise actual Job/candidate/source/identity/policy
lock contention, job cancellation with a cached Job, and committed changes to
cached source/policy/attempt/candidate authority. Blocking is observed through
`pg_blocking_pids` before releasing the competing transaction. The identity-wait
cases compare persistent state before and after the denied transaction. This is
an authorization prerequisite; controlled A1 byte execution and atomic provider
receipt handoff are still pending.

Final verification after the refresh fix: 99 PostgreSQL tests passed in 131.96 s
(the new clock cases plus discovery runtime/automation, publication authority,
worker admission/wait and provider cleanup/races). The two application handler
files passed 10 tests in 2.22 s. Both changed Python files passed strict mypy,
Ruff and formatting; the touched tracked implementation passed `git diff --check`.
Docker PostgreSQL remained the disposable loopback instance on port 1520.

The initial identity-wait fixture held an unrelated sync advisory lock and failed
to block twice. After checking the actual materialization path and documentation,
four options were compared: a shared advisory key, a conflicting table lock, a
SQLAlchemy statement-event barrier, and a lock on an existing artist external
reference. The final fixture uses the last option, a row the real materializer
already locks. Production behavior was not changed to accommodate the fixture.
Sources: [PostgreSQL locking](https://www.postgresql.org/docs/18/explicit-locking.html),
[blocking process inspection](https://www.postgresql.org/docs/18/functions-info.html),
[database clock](https://www.postgresql.org/docs/18/functions-datetime.html),
[SQLAlchemy events](https://docs.sqlalchemy.org/en/20/core/events.html), and
[Session refresh](https://docs.sqlalchemy.org/en/20/orm/session_basics.html).

A repeated Windows path-search mistake was resolved using discovered literal
paths and directory searches with `rg -g`; alternatives checked were `rg --files`
and `Get-ChildItem -Filter`. Wildcards are not supplied as literal native-tool path
arguments. Sources: [ripgrep guide](https://github.com/BurntSushi/ripgrep/blob/master/GUIDE.md)
and [PowerShell file filtering](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.management/get-childitem).

Independent read-only review identified the stale ORM-object case; the refresh
fix and four committed-writer regressions closed that finding. The reviewer did
not run tests. No production activation, migration, commit, push or deployment
was performed.

## Controlled A1 database handoff (2026-09-17)

`PostgresControlledDiscoveryRepository` now joins a durable exited provider receipt
to the existing A1 identity and Vault ingest path. It holds admission, the exact
source job, owner publication and candidate/lineage locks; validates the original
attempt, operator/source/artist authority and fresh provider evidence; rejects any
unclosed sibling provider for the same acquisition attempt; and binds only the
exact successful exit receipt from the current worker claim.

The receipt transitions EXITED -> SEALED -> HANDED_OFF in the transaction that
creates identity, the PROVIDER upload and its `vault.ingest` job. Failed identity,
enqueue or final validation leaves no half-binding. The legacy `prepare_ingest`
still rejects provider-prefixed files and shares only the extracted private
identity/upload construction core. No filesystem or network work runs inside
this handoff transaction.

Committed receipt replay precedes live source authorization, so a lost reply after
Vault completion does not request new bytes. READY clears the candidate staging
pointer; both COMMITTED and REUSED uploads are recognized through their remaining
exact receipt/upload/variant relationships. This closes the independent review's
deduplication replay finding.

Shared identity materialization now observes a track's recording mapping, locks
Recording before the external reference, and rechecks that mapping before reuse.
Existing user-reference and library rows are locked and refreshed. Controlled
handoff also rejects deleted/redirected targets and library removal after the
immutable selection, including a removal that wins a contended row lock. It does
not silently change to a newly redirected recording or recreate a concurrently
removed library item.

The new PostgreSQL proofs include atomic rollback, exact worker/evidence ownership,
live sibling rejection, owner/receipt wait expiry, valid identity reuse, competing
Recording/ref/library removal and redirect, cleanup waiting behind the real
handoff transaction, and actual VaultIngestHandler completion/replay for both
new and deduplicated CAS bytes. Provider process exit evidence is synthetic in
these database tests; the earlier Windows containment proofs remain separate.
This does not claim an actual Jamendo download or activate a production handler.

Final validation: 121 PostgreSQL tests passed in 151.19 s, including 22 controlled
A1 handoff cases and the 99 discovery/authority/admission/wait/cleanup regressions.
All four changed/new Python files passed strict mypy, Ruff and formatting. The
tracked discovery implementation passed `git diff --check`. These tests used the
disposable PostgreSQL instance on port 1520 and synthetic provider bytes/evidence.
Independent read-only review closed handoff/replay/identity/cleanup with no remaining
findings after the REUSED correction. The reviewer did not rerun tests. Production
activation, commit, push, deployment and production migrations were not performed.

## Controlled Jamendo execution and failure boundaries (2026-09-17)

The controlled A1 path now joins `ControlledDiscoveryAcquisitionHandler`,
`DiscoveryIoExecutor`, the retained process coordinator and the PostgreSQL handoff.
The fixed Jamendo child receives original track and artist IDs only after durable
GO. Both identities and download permission are checked before acquisition and
again after the verified copy. The existing provider workspace owns partial
downloads until exact exit permits durable cleanup.

`DiscoveryEvidence` carries bounded public metadata without a download URL.
Commands remain limited to 8192 bytes; the separate result limit is 64 KiB, enough
for the already-permitted maximum Unicode display values. The minimal launcher
passes only the explicit Jamendo client ID in addition to the existing Python
runtime environment. The result/error channel excludes the credential-bearing
download address, and child errors use an explicit stable-code allowlist.

The shared Jamendo adapter now rejects non-200 responses, ambiguous or unsupported
framing, incomplete declared bodies, incomplete chunked responses and oversized
input. Both JSON and audio check framing. Audio uses bounded reads, checks its
limit before writing, and flushes/fsyncs successful output. The controlled mode
retains partial files; legacy failure cleanup only removes a destination that this
call successfully created with exclusive creation. A pre-existing file survives.

The handler defers resource waits without spending provider retries, rebinds the
same logical operation after failure and releases it after the atomic handoff.
The shared executor still requires exact tree exit and durable closure before
returning a successful file receipt. Failure recording is also fenced: a database
failure while recording a network error remains retryable, stale policy authority
does not replace the original classified error, and bulk-row waits cannot commit
a candidate/attempt failure after job or source expiry. The final expiry checks
run after the failure transition's flush while mutable authority remains locked.

Eleven new Windows/PostgreSQL JobWorker cases passed with the real child and
Jamendo adapter using synthetic loopback API/audio responses: success, HTTP retry,
control/worker thread-start failure, database failure during error recording,
a download crossing both the 5-second IO and 30-second operation lifetimes,
artist changes before/after download, size rejection, repeated quota waits and
retirement of an incomplete download. Success binds one upload and one durable
HANDED_OFF receipt; retry retains the operation ID. The actual Windows descendant
tests now cover A1 as well as Internet: release, account/device revocation and a
lower global quota keep a live descendant charged after its root exits, until the
same retained tree's exit is acknowledged.

Windows child/adapter/legacy acquisition/automation/Vault/filesystem tests passed
103 cases; two symlink cases require unavailable Windows privilege. Linux source
tests passed 66 cases, including both symlink cases; the one skipped case requires
the Windows process-tree backend. This Linux run does not prove Linux containment.
The final PostgreSQL regression group passed 133 cases in 173.40 s: discovery
clock/failure/runtime/automation, handoff, publication/admission/wait, provider
cleanup and both providers' descendant proofs. Together with the 11 controlled
A1 cases and five controlled Internet cases verified separately against unchanged
relevant implementation, 149 distinct PostgreSQL cases passed in this batch.
All 21 changed Python implementation and test files passed strict mypy, Ruff and
formatting. The actual remote Jamendo API and credentials were not used.

An initial integration assertion expected short-lived execution rows to survive
`close_io()`. Five parameter cases exposed the same assumption. Three remedies
were compared: assert the durable closure receipt after completion, intercept the
closure transaction before accounting deletion, or capture an immutable test
snapshot through transaction events. The first matches the existing ownership
contract and preserves ordinary cleanup. Tests verify the durable child identity,
closure hash and successful exit while ephemeral accounting is gone. References:
[SQLAlchemy session lifetime](https://docs.sqlalchemy.org/en/20/orm/session_basics.html)
and [transaction events](https://docs.sqlalchemy.org/en/20/orm/events.html).

The contention observer could also miss a newly connected backend because
`pg_stat_activity` retains a transaction snapshot. Remedies compared were clearing
that snapshot before each observation, ending each read transaction, using an
AUTOCOMMIT observer, and enumerating current locks instead of activity. The first
keeps the original blocking dependency check and timeout. A real regression caches
the backend list, connects a new NullPool waiter, proves the old list omits it, and
then observes its actual row-lock dependency through the refreshed helper.
References: [PostgreSQL statistics snapshots](https://www.postgresql.org/docs/18/monitoring-stats.html),
[lock inspection](https://www.postgresql.org/docs/18/view-pg-locks.html), and
[SQLAlchemy AUTOCOMMIT](https://docs.sqlalchemy.org/en/20/core/connections.html).

Independent read-only review closed the failure-classification, stale-policy and
post-lock expiry findings, then verified the corrected A1 revocation fixture and
fresh lock observer with no remaining findings. The reviewer did not run tests.
No production activation, commit, push, deployment or production migration occurred.

## Successful provider scratch retirement (2026-09-17)

Successful HANDED_OFF provider receipts now support a separate scratch claim and
completion receipt. `ProviderScratchService` commits that claim before filesystem
work and completes it in a later transaction. Both repository mutations lock
admission before the provider row. Eligibility uses immutable successful handoff
and exact exit evidence, rejects an unclosed matching execution, and does not
depend on live account/source authority or an upload staging file still existing.

The scratch claim uses a distinct deterministic UUID namespace from abandoned
cleanup. The filesystem adapter moves the exact provider-work directory into
provider-retired with the existing no-replace rename; it does not touch upload
staging, quarantine or CAS. The move preserves bytes and is not disk-space purge.
Replays recover a rename whose completion was lost, including concurrent calls.
Missing source and destination fail without recording completion. Completed
receipts avoid further filesystem access. Pending keyset pages include claims
whose completion is unfinished; a new scan starts after the previous scan ends.

Migration `0041_provider_scratch` adds three nullable columns, a consistency check,
an immutable-receipt trigger and a partial pending index. Existing handoffs keep
null scratch fields. Downgrade takes ACCESS EXCLUSIVE before checking for claims
and refuses to discard even a completed claim. A real contended migration test
proves it waits for an in-flight claim and then refuses downgrade. Another test
downgrades an unclaimed handoff to 0040, upgrades again and retires its workspace
while preserving the original upload binding and bytes. The mapped inventory is
148 tables, 1678 columns and 142 explicit indexes; readiness expects 0041.

Windows filesystem/child tests passed 72 cases in 16.98 s with four symlink tests
skipped for unavailable privilege. Linux passed 75 cases in 18.64 s, including
all four symlink tests; one Windows backend case was skipped. These runs verify
source behavior and do not prove Linux process-tree containment.

The PostgreSQL migration/metadata and both providers' handoff files passed 71 cases,
including the full clean lifecycle, all 41 adjacent revision pairs, live Alembic
drift detection, and scratch retirement before and after actual Vault ingest.
Discovery verifies both COMMITTED and REUSED outcomes. The enclosing run then
found a stale test-clock helper in the staging rebind test: it shifted `lock()`
but left the subsequent `current_time()` read unchanged. The helper now shifts
the shared clock boundary while preserving the real admission lock. The final
staging/scratch/cleanup/execution/upload-exclusion group passed 79 cases in
88.28 s. Together these groups cover 150 distinct passing PostgreSQL cases,
including all 11 new scratch cases. Tests use disposable databases on port 1520
and synthetic provider evidence, not remote provider credentials.

All 16 affected Python files passed Ruff, formatting and strict mypy. The working
tree passed `git diff --check`. Independent read-only review closed the scratch,
replay and migration work; follow-up review also found no issues with the clock
helper correction and historical handoff migration test. The reviewer did not
run tests. No production activation, commit, push, deployment or production
migration was performed.

## Retained provider maintenance process (2026-09-17)

`ProcessProviderMaintenanceStorage` now supplies both existing provider retirement
storage ports through a fixed isolated Python child. Claim creation and completion
still belong to the application services. The parent does not initialize storage
or touch the configured Vault root; only a fully validated child GO command does.
The command is bounded to 8192 bytes and carries the exact provider execution,
claim, action and trusted root. The child starts no descendants and inherits no
database/session/provider credentials, proxy or executable search path. Successful
replies bind the same execution/claim/action; errors contain a fixed code only.

Migration `0042_provider_maintenance` adds a durable server-wide singleton execution
slot, independent of user TRANSFER capacity. The repository commits PREPARED before
spawn and RUNNING with the exact child identity before GO. It never clears the slot
on TTL, failed connection or another process's startup. The owner retains the same
Popen handle through exit and database acknowledgement; unresolved creation or exit
requires verified reconciliation. Database identity, child and closed evidence are
immutable, and downgrade refuses to discard any recorded maintenance ownership.
The inventory is now 149 tables, 1693 columns and 143 explicit indexes.

One local owner thread handles pipes and short database transactions. Its launch
gate prevents work before successful Thread.start, including an exception after
the thread actually started. A five-second caller deadline requests stop while
retaining unresolved work. An abnormal caller exit also requests stop and always
opens the gate safely. Failed work retains its original remaining error-response
window while exit acknowledgement settles; this does not renew I/O authority.
Only acknowledged exit permits the application service to complete an item after
a successful result. A failed child leaves its item claim retryable.

The initial Windows/PostgreSQL group passed 28 cases in 42.78 s: eight maintenance
cases, nine existing retained Vault/upload process regressions and eleven scratch
cases. Additional lost prepare/start response and thread-start tests exposed one
error-classification issue: stopping the failed child had prematurely discarded
the original error's response window. The final maintenance/provider-tree group
passed 19 cases in 36.21 s after correcting this and the review finding below.
It contains all 13 maintenance cases and six existing Internet/A1 descendant cases.

The 32 metadata/migration/quota-schema tests passed before that error-classification
failure in the enclosing run. They include the full empty-database lifecycle,
all 42 adjacent revision pairs, live Alembic drift detection, schema invariants and
PUBLIC privilege checks. Combining the unchanged passing groups and final rerun
gives 71 distinct PostgreSQL cases for this batch. All used disposable databases
on port 1520; real provider credentials were not involved.

Eight new Windows child/command/environment tests passed. Linux source tests passed
83 cases in 18.85 s with one Windows-only backend case skipped, including actual
maintenance child launches for both retirement actions and all existing symlink
cases. The disposable Linux container installed the pinned test dependencies and
a source-path .pth so isolated child interpreters loaded the current source, not
an older installed package. This does not establish Linux provider-tree containment.
All 18 affected Python files passed Ruff, formatting and strict mypy.

Independent read-only review found that interrupted caller waiting could abandon
the only stop loop. A finally block now requests stop, preserves the retained
owner and safely opens the launch gate on every exit. The new KeyboardInterrupt
test holds a real child alive, observes its exact PID/RUNNING state and proves a
second reservation remains blocked until the retained child exits. Follow-up review
closed the finding with no new issues. The reviewer did not run tests.

General reconciliation is still separate work: its inventory scans precede the
batch limit, quarantine is not yet fully replayable, and filesystem work remains
inside its database transaction. CAS claims must protect even orphan keys from
concurrent publication before moving bytes; an old missing-file observation is
insufficient authority to fail an upload. No production maintenance scheduling,
provider activation, commit, push, deployment or production migration occurred.

## Orphan CAS retirement claims (2026-09-18)

Migration `0043_orphan_object_claim` adds durable per-key retirement claims and
binds the existing singleton maintenance process to `ORPHAN_OBJECT` targets.
The current inventory is 150 tables, 1700 columns and 144 explicit indexes.
Both claiming and CAS preparation serialize on admission followed by the same
digest advisory lock. Any object with the digest or local filesystem replica
with the key prevents a claim, including STAGING/QUARANTINED metadata, missing
replicas, expired uploads and terminal jobs. No metadata deletion was introduced.

The fixed child accepts only the exact action/claim/storage-key/root command,
moves bytes with a no-replace hardlink and durable unlink to a claim-specific
quarantine name, and supports replay after an interrupted unlink. An unrelated
destination inode, unsafe path or absence of both source and destination fails.
Filesystem work occurs after claim and execution registration commit. The parent
retains its existing process handle and reservation until exact exit acknowledgement.
Completion requires the matching successful CLOSED process and no other active
execution of that claim. Completed claim replay cannot move later republished bytes.

The combined PostgreSQL group passed 73 cases in 102.84 seconds on disposable databases
using the owned PostgreSQL container at loopback port 1520. This includes 13 new
orphan cases, actual child launch/acknowledgement, both publication orderings,
observed lock blocking before uncommitted publication becomes visible, a second
PREPARED retry preventing premature completion, lost completion/child failure,
metadata protection, stale receipts after actual republishing, immutable evidence,
and concurrent downgrade refusal. Existing provider maintenance, scratch and Vault
tests also passed. Schema checks include the complete empty database lifecycle,
all 43 adjacent migration pairs, live Alembic drift detection and PUBLIC privileges.
Early test runs exposed two fixture construction errors (expiry before creation
and a missing mandatory MIME type); these were corrected without weakening constraints.

Windows child/filesystem tests passed 18 cases, with three symlink cases skipped
because the host cannot create symlinks. The Linux source run passed all 50 cases
in 4.94 seconds, including those three cases and actual isolated child launches.
It used the existing acquisition image with read-only source/test mounts and the
source-path `.pth` for isolated Python. This is not Linux provider-tree containment
evidence. All 23 changed Python files passed formatting, Ruff and strict mypy.

Bounded independent read-only implementation review found no defects in this
orphan-only path. It confirmed that current detach logic preserves object/replica
rows, which protect older publishers. Follow-up test review found that clearing
only one completion field could fail the shape CHECK even without the immutable
trigger. The corrected test clears both fields and requires the exact immutable
trigger error. Completion failure now covers both a failed transaction and a lost
response after its commit; the latter must not launch another retirement child.
All 14 orphan PostgreSQL cases passed again in 16.21 seconds, and follow-up review
closed with no new issues. Combined with the unchanged passing tests, this gives
74 distinct PostgreSQL cases. The changed test passed formatting, Ruff and mypy
again; `git diff --check` passed. The reviewer did not run tests.

Legacy `reconcile_inventory` still bypasses
claims and must not run destructive apply concurrently with the new path. This
batch does not wire production scheduling, replace tracked-CAS/staging reconciliation,
or close full Admin/accounts acceptance. No commit, push, deployment, provider
activation, real credentials or production migration occurred.

## Remaining acceptance

Provider handlers are not activated. Linux process-tree containment,
production scheduling for the retained provider maintenance adapter,
actual provider runtime/credentials checks and production composition remain necessary. The controlled
Internet and A1 paths above now join the Windows backend, coordinator, durable wait and
pre-handoff staging ownership, but this is not full worker-path rollout. Existing reconciliation filesystem work still
runs inside its database transaction and needs a separate bounded maintenance path
before activation. Android resource
coordinators, recovery/deletion/consent, actual target capacity measurements and the full
laptop/A55/private-network acceptance remain open. These batches do not close the full
Admin/accounts acceptance criteria.

# Internal ingest ownership: 2026-09-18

Migrations `0047_ingest_execution` and `0048_ingest_cleanup` add durable internal
process ownership for ingest and independently retained post-finalize cleanup.
They are a foundation for the unfinished Admin/accounts goal. The final section
records subsequent measured-admission infrastructure and worker composition in
`0049_internal_io_budget`; it supersedes the earlier integration gaps below.
No production measurement, commit, push or deployment occurred. All validation
databases were disposable.

## Implemented behavior

`vault.ingest_execution` binds the execution UUID, owner run, upload, staging key
and exact job/worker/attempt. PREPARED commits atomically with PROCESSING. Partial
unique indexes exclude another unfinished execution for the upload and staging key.
The receipt survives expired/recovered jobs and terminal upload states. SQL guards
freeze its identity and closure evidence and the upload's input/owner/source/job
bindings. Downgrade refuses to discard any ownership, including CLOSED receipts.

Start registers the exact child and validates the current ingest job/source again.
RUNNING replay and renewal perform the same checks. I/O authorization is bounded
by five seconds and the job deadline. An expired authorization cannot be revived:
the repository checks old deadlines with fresh database time, and the SQL trigger
checks again at the actual UPDATE. A delayed update therefore cannot extend an
authorization which expired after the Python check. Failed transitions roll back
the complete tuple. Inspection and prepare replay do not independently authorize GO.

`IngestSession` carries expected execution identity through start, prepare, finalize
and quarantine. An omitted expectation rejects unclosed ownership; a supplied
expectation requires the exact RUNNING receipt/child, even if no other row exists.
Checks precede terminal replay and run again after metadata flush. HTTP upload
writers and terminal-upload cleanup use the same exclusion predicate. Source
revocation stops work but still permits fenced quarantine and eventual exit ack.

The retained process/supervisor accepts internal ingest tickets and requires a
fixed launcher plus Windows Job Object or Linux cgroup containment. A root which
exits while its detached descendant lives cannot produce tree-exit evidence.
Neither job recovery nor an unavailable acknowledgement frees the receipt or local
supervisor entry. Exact exit acknowledgement does not require a live job/source.
It never marks staging cleanup complete or consumes an audio transfer permit.

The 0048 registry has 154 tables, 1750 columns and 149 explicit indexes; its
mapping fingerprint is
`8cca0a5905d998bcd19c6b6a0f854b6d3e6287b2156a9aaf1324e98dbe6dba09`.

## Evidence and review

The PostgreSQL tests cover job recovery with PREPARED ownership, all four metadata
boundaries, cancellation, expiry both before renewal and during start/renew, direct
SQL identity rewrites, downgrade preservation, actual detached descendants,
failed durable acknowledgement, source generation revocation, and finalization
which leaves staging present. The delayed SQL-write regression checks that the
old deadline has expired while the proposed replacement is still future.

Independent read-only review closed the first-start autoflush and renewal-expiry
findings. First start now assembles the full RUNNING tuple before flushing. The
expiry checks and SQL update guard preserve ownership while denying further work.
The reviewer did not run tests.

Verified runs:

- Final Linux: **48 passed in 76.83 s**, no skips (17 cgroup cases, seven provider
  database/tree cases and 24 ingest cases), UID/GID 10001, CapEff=0.
- Final Windows PostgreSQL: **35 passed in 85.19 s** (24 ingest, six metadata and
  five migration close gates, including live Alembic drift and every adjacent pair).
- Adjacent PostgreSQL: **58 passed in 172.81 s**, covering existing Vault, HTTP
  writer exclusion, terminal cleanup, Internet/A1 publication and provider trees.
  Existing ingest lease fencing also passed its 13 cases in the earlier batch.
- Windows process/service/maintenance units: **60 passed, one symlink privilege
  skip in 10.86 s**. Linux's real filesystem/tree cases did not skip.
- Ruff/format and strict mypy passed on 23 changed Python files for Windows and
  Linux targets. `git diff --check` passed; unrelated existing line-ending notices
  were left alone. The final added SQL timing assertions received their own targeted
  regression check, without restarting the broad batches.

Reproduction uses the locked dependencies and disposable PostgreSQL container
`autplay-admin-20260916-postgres-1` on loopback port 1520:

```powershell
$env:AUTPLAY_TEST_DATABASE_URL='postgresql+psycopg://autplay:autplay_dev_only@127.0.0.1:1520/autplay?connect_timeout=3'
uv run --project server --frozen python -m pytest -c server/pyproject.toml server/tests/postgresql/test_ingest_execution.py server/tests/postgresql/test_metadata.py server/tests/postgresql/test_migration_close_gates.py -q -x --tb=short -W error::sqlalchemy.exc.SAWarning
```

For Linux use the private delegated cgroup recipe in
[the Linux containment record](ADMIN_LINUX_CONTAINMENT_2026_09_18.md), adding
`/repo/server/tests/postgresql/test_ingest_execution.py` to the pytest arguments.
The frozen dependencies are installed in `/opt/reconcile-venv`; source mounts are
read-only. Tests run at UID/GID 10001 with CapEff=0. The setup-only SYS_ADMIN
capability is not a production requirement imposed by the adapter.

## Remaining integration

At the 0048 checkpoint, `worker_cpu.py` still used direct storage/media calls. The contained WORK coordinator
below is implemented and tested, but is not connected to that production entrypoint.
Its callback owns the complete metadata lifecycle as well as the child pipe.
Current start/renew covers work only; terminal COMMITTED/REUSED uploads cannot use
it to start another publishing phase.

Canonical post-finalize staging-cleanup intent and a separate retained cleanup
phase/replay are now implemented, separately from DEVICE CANCELLED/EXPIRED claims.
Next connect the owned phases, measured internal CPU admission, and shutdown/once
draining. Neither per-process worker concurrency
nor the existing PLAYBACK/TRANSFER measurement report establishes this CPU budget.
Drain all unregistered legacy workers before the first registered writer is enabled.

## Contained WORK implementation

`runtime/ingest_io.py` now retains the whole metadata/pipe callback, with separate
renewal and watchdog threads. Registration precedes spawn and durable RUNNING
precedes GO. Local permission is anchored at RPC start and capped to the actual
server grant, including a job lease shorter than five seconds. Delayed renewal
cannot revive a stopped child. Freezing renewal settles an in-flight RPC before
terminal metadata, while the independent watchdog continues enforcing the last
grant. Shutdown returns pending identities rather than claiming uncertain exit.

`filesystem/ingest_child.py` and `ingest_process.py` implement fixed capacity,
verify, analyze, publish and finish phases. Only the contained child opens Vault
paths or runs media executables; the parent receives bounded metadata/fingerprint
frames. The launcher inherits no database, session or proxy credentials. WORK
cannot clean staging. The separate CLEANUP command/storage primitive verifies
matching CAS bytes and stable existing directories before unlinking, but has no
durable cleanup claim/coordinator yet and is not enabled by WORK or a scheduler.

Publication validates shard directories before creating or linking entries, and
fsyncs every CAS directory level on both first publication and replay. Missing
mounts are not initialized by ingest. Cleanup also syncs the verified CAS chain
before deleting staging and syncs an already-absent staging leaf on replay.

Independent review found an uncertain-prepare absence race. `reconcile()` now
acquires the same admission lock as prepare before reading an absent receipt,
preventing a still-committing PREPARED row from losing its retained owner. The
regression observes a real PostgreSQL lock wait via `pg_blocking_pids` before
releasing the original transaction. Review is closed; the reviewer ran no tests.

Verified affected batches after implementing the complete WORK block:

- Windows: **91 passed, four environment skips, 9.79 s**. The skips are three
  unavailable symlink cases and the Linux-pinned real-media case.
- Linux: **129 passed, no skips, 67.97 s**: 95 protocol/filesystem/process/service
  cases, 27 real PostgreSQL ingest cases and seven provider process/tree cases.
  Includes actual FFmpeg 8.1.2 and fpcalc 1.6.1, detached descendants, blocked
  renewal/pipe, shortened grants, whole-callback settlement and lost exit ack.
- Ruff/format and strict mypy passed on 17 affected Python files, with Windows
  and Linux typing targets. That WORK-only checkpoint added no schema migration;
  its head was 0047. The cleanup implementation below advances the head to 0048.

The prior acquisition proof image lacked fpcalc, causing the initial Linux batch
to stop at the real-media case. The final local image `autplay-ingest:proof-20260918`
adds the exact Dockerfile-pinned Chromaprint archive and verifies its binary SHA-256
`e7b14fbf9d544f6ba99b7aced3c07786258e09e37cfcb054a41d2a6eeb0887a7`.
The archive SHA-256 is
`fc16cd37a70168040bc9ceb45f1d4d1216f5a75bc4c9cf8564bea70ac6a45733`.
Use the private cgroup setup from the Linux containment record, install frozen
dependencies in `/opt/reconcile-venv`, and run these pytest files at UID/GID 10001
with CapEff=0, read-only source mounts and the disposable PostgreSQL database:

```text
tests/test_ingest_child.py
tests/runtime/test_ingest_io.py
tests/runtime/test_resource_io_deadline.py
tests/test_vault_filesystem.py
tests/test_vault_child.py
tests/test_provider_maintenance_child.py
tests/test_vault_services.py
tests/postgresql/test_ingest_execution.py
tests/postgresql/test_provider_process_tree.py
```

The Windows batch uses the first seven files with `uv run --project server --frozen
python -m pytest`. All test sessions terminated. No production activation,
migration, commit or deployment occurred; the full Admin/accounts goal remains open.

## Atomic finalization and retained cleanup

Migration `0048_ingest_cleanup` atomically queues a canonical claim when registered
WORK finalizes COMMITTED/REUSED. It freezes the upload result and binds the exact
WORK execution, staging key, expected size/hash, object and variant. A finalization
rollback also removes its claim. Legacy terminal metadata is not backfilled.
SQL guards exclude cleanup and HTTP/WORK writers in both directions; downgrade
refuses any cleanup claim or execution history.

`IngestCleanupCoordinator` uses the retained coordinator lifecycle with separate
cleanup authority. It waits for exact acknowledged WORK process/tree exit, including
nonzero exit after successful finalization. Its own short grants do not depend on
the old job or source authorization. The fixed child verifies and syncs canonical
CAS bytes before deleting and syncing staging. Completion requires acknowledged
PROCESS_EXIT with code zero and no competing unclosed writer. A lost completion
reply replays from the completed claim without launching or touching files.

`ControlledVaultIngestHandler` composes the whole registered WORK callback with
this cleanup service. A cleanup failure leaves a durable intent; it cannot reopen
publication. Retained HTTP upload/WORK/cleanup blockers enter durable RESOURCE_WAIT
without consuming retry budget. Both local registries share a bound of 100 entries,
and the deferral query accepts their combined bounded list of 200.
At that checkpoint the injectable handler was not enabled in `worker_cpu.py`;
measured internal admission and shutdown/once draining were still open.

Verification of this complete block covers **153 distinct passing cases** across
the platform runs, not a single full-suite run:

- Linux passed 68 cases in 47.24 s before a pytest collection-scope error, including
  all 18 new PostgreSQL cleanup cases and the actual pinned media WORK-to-CLEANUP
  path. The remaining PostgreSQL batch passed 74 cases in 86.54 s before exposing
  one missing expected foreign-key name in the metadata test. No application
  implementation changed in response to either interruption.
- After correcting that expected name, the 13 metadata/migration cases passed in
  36.36 s against disposable PostgreSQL 18.4. This includes live Alembic drift,
  all 48 adjacent migration pairs, clean upgrade/downgrade/upgrade and privileges.
  The first two metadata cases overlap the 74-case batch; counts above deduplicate
  them. Previously passing behavioral groups were not rerun for this test-only fix.
- Windows process/cleanup tests passed **40**, with three environment skips, in
  32.71 s. Linux passed those skipped real-media and symlink cases. Including the
  migration batch gives 53 Windows passes. Linux has 142 distinct passing cases.
- Ruff, formatting and strict mypy passed on all 37 affected files for Windows
  and Linux targets. The two subsequently edited test files passed targeted static
  checks again. `git diff --check` passed for the touched tracked paths.
- Bounded independent read-only review closed both findings: missing HTTP-writer
  deferral and the inconsistent combined registry bound. Both have PostgreSQL
  regressions. The reviewer ran no tests and changed no files.

The Linux image and private cgroup/UID10001 recipe above remain applicable. Keep
all PostgreSQL file arguments together: pytest 9.1.1 has a documented
[interleaved-directory fixture regression](https://github.com/pytest-dev/pytest/issues/14635).
The interrupted batch was resumed from the unrun files; a mistakenly constructed
intermediate retry was stopped and contributes no evidence count. An initial
shared-bytecode hypothesis was not the fix. No dependency version was changed.

For reproduction, use these PostgreSQL selectors as one contiguous group:

```text
tests/postgresql/test_ingest_cleanup.py
tests/postgresql/test_ingest_execution.py
tests/postgresql/test_vault_ingest_fence.py
tests/postgresql/test_internet_ingest_authority.py
tests/postgresql/test_resource_upload_exclusion.py
tests/postgresql/test_metadata.py
tests/postgresql/test_migration_close_gates.py
tests/postgresql/test_migrations.py::test_clean_upgrade_downgrade_and_upgrade_again
tests/postgresql/test_migrations.py::test_every_revision_has_one_linear_predecessor
```

The affected non-database group is `test_ingest_child.py`, `test_vault_services.py`,
`runtime/test_ingest_io.py`, and `runtime/test_resource_io_deadline.py` under tests.
The Windows-specific group used the new cleanup file, ingest runtime and child
tests. All test sessions are terminal; only the pre-existing disposable database
container remains. No production migration or activation occurred.

Repeated patch-context misses were resolved by reading fresh exact context and
using unique anchors. Alternatives considered were anchored smaller hunks,
whitespace handling, and literal replacement for mechanical head changes; see the
[Codex patch format](https://github.com/openai/codex/blob/main/codex-rs/prompts/templates/apply_patch_tool_instructions.md)
and [git apply options](https://git-scm.com/docs/git-apply). File discovery uses
`rg --files` before reading guessed names; alternatives are existence checks and
listing the verified parent directory. None of these diagnostics touched `.codex-state`.

## Shared internal capacity and actual CPU worker composition

Migration `0049_internal_io_budget` adds an initially unconfigured global internal
policy. Every unclosed WORK, finalized-cleanup and provider-maintenance execution
counts until exact durable closure. Python admission and SQL INSERT triggers share
one advisory lock around capacity checks and PREPARED insertion. Lowering the limit
retains existing charges and permits otherwise valid renewal/closure. Epoch loss
denies new admission without preventing cleanup of existing ownership. Maintenance
and inventory reconcile uncertain PREPARED absence under that same lock.

Local `internal-io-budget-apply` and `internal-io-limit-set` use revision CAS, exact
operation replay and atomic audit. Joint reviewed evidence must cover audio ceilings,
all eight internal paths, positive useful throughput/completions and the worst
permitted simultaneous mix. See the expanded measurement report contract. These
checks validate evidence structure; they do not fabricate target-server measurements.

`worker_cpu.py` now constructs contained WORK and finalized cleanup, with an
independent resource-control pool and Windows Job Object/Linux delegated-cgroup
trees. Jamendo uses its controlled acquisition executor and preserves the configured
download-size ceiling. --once and continuous operation both handle signals through
an independent observer; handlers stay installed during the shared five-second
shutdown/drain budget. Unconfirmed children retain durable charges. A poisoned
cleanup claim remains pending while keyset traversal and job polling continue.

The metadata worker still performs uncontained byte work and now refuses startup
when internal capacity is configured. This is a temporary integration restriction,
not all-path activation. Drain legacy workers before enablement; an old running
metadata worker cannot be discovered or stopped by the new startup guard. Target
measurements, production delegation and remaining byte paths are still unfinished.

The current head is `0049_internal_io_budget`: 155 tables, 1763 columns and 149
explicit indexes. Mapping fingerprint:
`fc6a3f853eeae10074643fe0e2af2617ebc26e5840e28dad5ea995ce339baee6`.

Verification was performed after implementing the functional block:

- Linux: **170 passed, no skips, 295.82 s**, using the pinned media/cgroup proof
  image and UID/GID10001, CapEff=0. It includes actual worker --once publication
  and cleanup, shared-budget races, lowering/epoch changes, locked reconciliation,
  poisoned cleanup, startup refusal, ownership regressions and settings/entrypoints.
  Migration checks include clean lifecycle, live drift, PUBLIC privileges and all
  49 adjacent pairs.
- Windows: **37 passing cases** across affected groups. Eleven existing controlled
  Jamendo cases passed before a new test exposed an incorrect expected outcome:
  oversized downloads are terminal, as required by the existing provider contract.
  Its corrected regression, final signal-during-drain test and control-pool tests
  passed together (7 cases, 21.30 s). Audit rollback passed both mutators (2 cases,
  12.56 s). Seventeen selected existing reconciliation/publication cases passed
  (31.59 s) after explicitly supplying their newly required synthetic capacity.
  Previously passing groups were not restarted for these test-only corrections.
- Ruff/format and strict mypy passed on 47 affected files for Windows; strict mypy
  also passed on the 43 implementation/core-test files for Linux. The last added
  audit tests and four fixture-only changes received targeted static checks.
  `git diff --check` passed. Existing adjacent line-ending notices were untouched.
- Bounded read-only review closed shutdown propagation/restoration, poisoned-claim
  fairness and provider-size-limit findings. The reviewer did not edit or run tests.

Linux selectors were contiguous PostgreSQL files: `test_internal_io.py`,
`test_ingest_cleanup.py`, `test_provider_maintenance.py`,
`test_vault_inventory_process.py`, `test_upload_cleanup.py`, `test_orphan_missing.py`,
`test_metadata.py`, `test_migration_close_gates.py`, and the clean-lifecycle/linear-chain
cases from `test_migrations.py`; followed by runtime `test_ingest_io.py`,
`test_cpu_entrypoints.py` and `test_settings.py`. Use the same private cgroup recipe
above. Windows exercised controlled discovery, resource-control runtime, final
signal/audit cases and selected reconciliation call sites. All sessions terminated.
No deployment report, production migration or scheduler activation was performed.

# Bounded Vault inventory foundation: 2026-09-18

This records local implementation slices of the continuing full Admin/accounts
goal, not full tracked-byte reconciliation or production activation. The initial
foundation required no migration; the follow-ups below add
`0044_inventory_maintenance` and `0045_orphan_missing` (current head).
No commit, push, deployment, real credentials, or
`.codex-state` changes were made. Adjacent working-tree changes remain in place.

## Implemented and checked

`FilesystemVaultInventoryCursor` retains a live directory traversal across pages.
Each page accepts 1-100 work units; directory opens, entries, and exhaustion
boundaries consume budget, including empty directories and vanished files. It
does not sort/materialize an entire directory, rescan preceding pages, read file
payloads, or traverse quarantine/provider workspaces. At most three enumeration
handles are live. Cancellation and traversal failures close them. Fresh no-follow
metadata checks reject symlinks/reparse points, malformed CAS layout, and changes
to the identity of already-open directories between pages. This is observation,
not a filesystem security sandbox against arbitrary concurrent namespace mutation.

A live iterator is deliberate: directory enumeration order is arbitrary, and
concurrent additions/removals need not appear. A key-based resume token would not
make a complete or atomic inventory. See the official
[Python scandir documentation](https://docs.python.org/3.14/library/os.html#os.scandir)
and [Windows enumeration lifecycle](https://learn.microsoft.com/en-us/windows/win32/fileio/searching-for-one-or-more-files).
The `exhausted` field means this traversal reached its end; it never proves that
an upload's file is missing. Restarting the cursor starts a fresh traversal.

`PostgresVaultInventoryRepository` classifies only positive entries from one
bounded page. Any object metadata or local replica registration protects a CAS
key regardless of state. Provider and upload staging are reported as owned;
unknown staging remains observation-only because it may belong to an
uncommitted API upload or legacy `disc-*` writer. Its short-lived session is
closed before any subsequent filesystem retirement.

`BoundedVaultInventoryService` optionally feeds orphan candidates through the
existing durable orphan claim and retained maintenance child. Claim acquisition
rechecks current publication ownership. Scan and classification are not cleanup
authority. A failed batch retains its bounded page, unfinished position, and
canonical claim receipt before filesystem execution. A database reply lost after
completion therefore replays the same claim, including when a requested ID joined
an already-active claim with a different ID.

## Evidence

- Final PostgreSQL batch: **27 passed in 23.81 s**, consisting of 13 new inventory
  cases and all 14 existing orphan retirement cases. Tests use fresh generated
  databases in the owned disposable PostgreSQL 18.4 container at loopback port
  1520. SQLAlchemy warnings are errors.
- Windows inventory/orphan batch: **21 passed, 7 skipped in 2.22 s**. The skipped
  cases require symlink creation unavailable on this host; four belong to the
  inventory scanner and three to existing orphan retirement.
- Linux inventory scanner: **15 passed in 0.42 s**, including all four new
  symlink cases, in `autplay-acquisition:perf-gate-20260914` with read-only source
  and test mounts and no network. Existing Linux orphan evidence is recorded in
  `ADMIN_RESOURCE_WORKERS_2026_09_16.md`; it was not rerun in this slice.
- Ruff and strict mypy passed on all six affected implementation/test files.
  Formatting and `git diff --check` passed.
- Bounded independent read-only review found two retry defects (lost page and
  canonical claim alias); both were fixed and regression-tested. Follow-up review
  closed with no remaining actionable finding in this foundation scope. Tests
  were run by the implementing agent, not by the reviewer.

PostgreSQL cases include a real uncommitted upload with existing staging bytes,
protected provider receipts after accounting removal, every registered object
state without replicas, replica keys differing from object digests, publication
after observation, absent staging without upload failure, actual retained orphan
child exit, zero checked-out classification connections at scan/retirement, and
failed-page retries after metadata failure or completion-response loss. Final
test sessions terminated and their maintenance shutdown assertions found no
pending child.

## Required continuation

The sequential CLI follow-up below now replaces the old destructive
`PostgresVaultRuntime.reconcile_inventory` method and caller-owned Vault UoW.
Live scan/claim, exact scanner exit, durable pending replay and explicit MISSING
resolution are connected. No scheduler or production maintenance was activated.

1. Add durable ownership for all actual staging/CAS writers, especially upload
   creation and `vault.ingest`, then implement tracked retirement/missing-file
   decisions through a committed claim and exact process closure. Current resource
   execution kinds do not provide ingest-exit evidence. Terminal jobs or expired
   leases remain insufficient. Controlled provider cleanup/scratch already have
   their own claim protocols and should continue using those.
2. Implement tracked CAS integrity/missing decisions and tracked staging cleanup
   through those ownership protocols, then extend the CLI's currently explicit
   `unregistered_cas` coverage. Metadata observations, terminal jobs and generic
   filesystem failures remain insufficient authority. Keep the finalizer rejection
   invariant covered independently of scanner side effects.
3. Finish all worker/HTTP composition, containment and target measurements in the
   full Admin/accounts goal before claiming all-path admission. A CLI test against
   the disposable database is not production deployment or a physical-device test.

## Reproduction

From the repository root, using the existing disposable container only:

```powershell
$env:AUTPLAY_TEST_DATABASE_URL='postgresql+psycopg://autplay:autplay_dev_only@127.0.0.1:1520/autplay?connect_timeout=3'
uv run --project server --frozen python -m pytest -c server/pyproject.toml server/tests/postgresql/test_vault_inventory.py server/tests/postgresql/test_orphan_object_retirement.py -q -x --tb=short -W error::sqlalchemy.exc.SAWarning
uv run --project server --frozen python -m pytest -c server/pyproject.toml server/tests/test_vault_inventory.py server/tests/test_orphan_object_retirement.py -q -x --tb=short
docker run --rm --read-only --tmpfs /tmp:rw,nosuid,size=256m --network none -v 'D:/AutPlayProd/AutPlay/server/src:/source:ro' -v 'D:/AutPlayProd/AutPlay/server/tests:/proof:ro' -e PYTHONPATH=/source autplay-acquisition:perf-gate-20260914 /opt/acquisition/.venv/bin/python -m pytest -c /dev/null -p no:cacheprovider /proof/test_vault_inventory.py -q -x --tb=short
```

The Linux scanner test has no isolated child imports; the older retained-child
Linux suites still need the installed source `.pth` described in the continuation
record. Use `python -m mypy` for typing; the direct `uv ... mypy` trampoline failed
to canonicalize its script path on this host, and module invocation passed.

## Retained scanner follow-up (2026-09-18)

`ProcessVaultInventoryCursor` now keeps a single isolated scanner alive across
bounded pages. Migration `0044_inventory_maintenance` adds the `INVENTORY` action
to the existing singleton, with `claim_id=execution_id` and no provider/object
target. The same immutable identity/exit guards apply. Downgrade refuses any
inventory ownership history. Table/column/index counts do not change; the model
definition fingerprint and expected migration head were updated and checked.

Only the pipe-owner thread communicates with the child. An independent watchdog
can request stop while that worker is blocked in PostgreSQL or a pipe. Idle
renewal and each page validate the exact RUNNING ticket and child identity before
renewing the existing irreversible deadline. Stop, timeout, lost DB responses,
partial thread startup, or parent loss never expire the durable reservation.
Neither a returned last page nor requested termination counts as exit proof.
Final success waits for actual zero exit and durable acknowledgement; all closure
paths retain the handle until that acknowledgement and pipe-owner completion.

The private protocol has sequence/identity/type checks and an explicit 32768-byte
reply bound for up to 100 entries. The default 8192-byte JSON limit would be too
small. The child inherits no application credentials, performs no work before
GO/page permission, and launches no descendants. Closing during reply decoding
prevents late successful page delivery.

Current evidence:

- **88 distinct PostgreSQL cases covered by passing results**: 33 unchanged
  maintenance/orphan/process cases in the initial combined run; 32 schema and
  migration cases in a final run (53.00 s); and 23 final scanner/scratch cases
  (40.75 s). The latter includes all 12 scanner cases. The first combined run
  stopped at the expected old metadata fingerprint; after updating the fingerprint,
  all schema/migration cases passed, including live drift, all 44 adjacent
  migration pairs, and PUBLIC privilege checks. Collection confirmed the union.
- Windows scanner/inventory/maintenance/orphan group: **41 passed, 7 symlink cases
  unavailable in 2.64 s**.
- Linux same four-file group: **48 passed in 3.04 s**, including actual isolated
  scanner/retirement launches, large reply frames, and every symlink case.
- Ruff, formatting, and strict mypy passed on all **19 affected Python files**;
  `git diff --check` passed. All test processes completed and shutdown checks
  found no pending runtime children.
- Bounded independent implementation review is closed. One page/close delivery
  race was fixed and exercised by a deterministic decoder barrier test. The
  reviewer made no edits and did not run the tests.

Scanner tests additionally exercise blocked pipe reads, a last reply from a child
that has not exited, ineffective kill requests, a second caller/maintenance owner,
idle renewal blocked in PostgreSQL, late status replies after local expiry,
committed launch/closure reply loss, exact exit acknowledgement delays, partial
thread start, and scanner-history downgrade protection.

For Linux, the disposable image initially lacked settings dependencies needed by
the retained-process imports. Installed only lock-matching dependencies into the
throwaway container with `uv pip install --no-deps`: `annotated-types==0.8.0`,
`greenlet==3.5.5`, `pydantic==2.13.4`, `pydantic-core==2.46.4`,
`pydantic-settings==2.15.0`, `python-dotenv==1.2.2`, `sqlalchemy==2.0.52`,
`typing-extensions==4.16.0`, and `typing-inspection==0.4.4`. An `autplay_source.pth`
in that container's site-packages points to the read-only `/source` mount so `-I`
children import current code. No host dependencies or image tags were modified.

After recurring E501 lint findings, consulted the official
[formatter guidance](https://docs.astral.sh/ruff/formatter/),
[E501 rule](https://docs.astral.sh/ruff/rules/line-too-long/), and
[settings](https://docs.astral.sh/ruff/settings/). Considered formatter wrapping,
manual splitting, and changing/suppressing the line limit. Used formatting for
ordinary code and manual splitting for the embedded child script; lint limits
and assertions remain enabled.

The next integration steps remain listed above. In particular, a retained cursor
does not itself replace the legacy CLI/UoW, provide durable scan/claim replay, or
establish tracked ingest writer ownership. The following slice provides an
explicit resolution service for missing-both orphan claims.

## Explicit orphan absence outcome (2026-09-18)

Migration `0045_orphan_missing` adds the check-only `ORPHAN_MISSING` maintenance
action with the same exact orphan claim/key and global singleton. It extends the
existing completion guard; no columns, tables or indexes are added. Downgrade
locks both ownership tables and refuses any history of this action, including
already closed or never-started runs. The model fingerprint is now
`562cb0faba77b1794c65d4f54d032b8232c15db2baf51e705442a81649b59686`.

The fixed maintenance child checks the CAS source and this claim's quarantine
destination, performs no mutation, and returns its exact action/target identity.
Both leaves must be absent; safe root, object, shard and quarantine directories
must remain present with the same identities before and after the checks.
Symlinks/reparse points, replaced or missing directories, permission and I/O
errors fail closed. The trusted Vault namespace assumption is unchanged; these
checks are not a sandbox against arbitrary hostile namespace replacement.

`OrphanObjectClaim.outcome` exposes `RETIRED` or `MISSING`, derived from the
immutable completed maintenance execution. Both require acknowledged exact
process exit with code zero and no unfinished competing execution for that claim.
`complete()` accepts only retirement; `resolve_missing()` accepts only the
check-only action, including on completed-row replays. Filesystem retirement
continues to reject the missing-both case. Service retries use a canonical claim
ID retained before execution; completed MISSING never counts as retired, and
completed RETIRED cannot be relabeled missing. Old receipts do not launch a new
child or touch a later publication of the same digest.

The new service is not automatically invoked after arbitrary retirement errors.
Sequential scan/claim/drain composition and durable pending replay remain the
next local integration work. Unknown staging and tracked CAS still require the
writer protocols described above; no historical writer exit is inferred here.

Final evidence for this slice:

- PostgreSQL **108 passed** in two disjoint batches: 65 maintenance/orphan/inventory
  cases in 72.40 s and 43 schema/migration/scratch cases in 75.12 s. Includes all
  13 new absence cases, all 45 adjacent migration pairs, clean upgrade/downgrade,
  live metadata drift and PUBLIC privilege checks. SQLAlchemy warnings were errors.
- Windows five-file child/filesystem group: **50 passed, 10 skipped in 2.98 s**.
  All skips are unavailable symlink creation on this host.
- Linux the same five-file group: **60 passed in 4.35 s**, including every symlink
  case and actual isolated child commands. Used the disposable dependency/source
  setup described above; no image tag or host dependency changes.
- Ruff, formatting and strict mypy passed on the **18 affected Python files**;
  `git diff --check` passed. The changed fingerprint is intentional; table/column/
  index counts remain 150/1700/144. One line-ending formatting fix was applied
  after updating that fingerprint, without changing test behavior.
- Bounded independent design and implementation review closed with no remaining
  actionable defects. The design review prompted the pre/post directory identity
  checks. The reviewer made no edits and did not run tests. Production composition
  was outside this review. All reported test sessions completed and runtime
  shutdown checks left no pending child.

The PostgreSQL cases cover stale observations after an earlier claim moved bytes,
later publication followed by an inert old receipt replay, joined canonical claim
IDs with lost completion replies before/after commit, cross-action completion
rejection before/after completion, unconfirmed/nonzero exits, a competing pending
run, present source/quarantine bytes, delayed exit acknowledgement, and downgrade
waiting for an uncommitted check-only run. Filesystem tests additionally inject
permission/I/O errors and directory disappearance/replacement during leaf checks.

On the recurring E501 finding, revisited the official Ruff sources linked above.
Considered formatter wrapping, manual literal splitting, and changing/suppressing
the limit; split the SQL literal manually. Existing lint rules remain unchanged.

Reproduce the current PostgreSQL groups by combining these paths after the same
locked `pytest` command and disposable database URL shown above:

```text
server/tests/postgresql/test_orphan_missing.py
server/tests/postgresql/test_orphan_object_retirement.py
server/tests/postgresql/test_provider_maintenance.py
server/tests/postgresql/test_vault_inventory.py
server/tests/postgresql/test_vault_inventory_process.py
server/tests/postgresql/test_metadata.py
server/tests/postgresql/test_migrations.py
server/tests/postgresql/test_migration_close_gates.py
server/tests/postgresql/test_resource_admission_schema.py
server/tests/postgresql/test_provider_scratch.py
```

The Windows/Linux group is `test_orphan_missing.py`,
`test_orphan_object_retirement.py`, `test_provider_maintenance_child.py`,
`test_vault_inventory.py`, and `test_vault_inventory_child.py` in `server/tests`.

## Sequential reconciliation CLI (2026-09-18)

`VaultReconciliationService.steps()` now drives one live scanner through bounded
pages and uses short classify/claim transactions. APPLY first drains durable
pending claims, scans/claims positive unregistered CAS entries, waits for exact
scanner shutdown acknowledgement, then drains new pending claims. The scanner and
retirement never concurrently consume the singleton. Each instance is single-use;
abandonment closes the generator/scanner. A restart drains saved claims and scans
afresh, without pretending that a dead scandir iterator has a durable resume token.
`run()` explicitly closes its generator even if its caller is interrupted between
yields. No final successful report precedes the shutdown gate.

Pending drain uses bounded UUID keyset pages and canonical claim receipts.
A poison claim can be deferred without starving later claims. Final pending state
uses a separate bounded existence query: reaching the keyset end does not exclude
deferred claims or UUIDs concurrently inserted behind the cursor. Only a closed
`maintenance_storage_failed` operation with an empty local retained-process set
may lead to a separate `ORPHAN_MISSING` check. Busy/deadline/database failures and
unconfirmed exits abort; they never imply absence. Lost claim/completion replies
recover through durable pending receipts on the next invocation. This does not
adopt another process's unresolved execution or clear its reservation.

The actual `autplay-admin vault-reconcile` composition now uses the short resource
control engine, retained scanner and retained orphan maintenance adapters. The
old DB-runtime `reconcile_inventory` implementation and its failure-only source
repair helper have been removed, with no fallback. No caller-owned Vault UoW or
parent filesystem adapter surrounds the command. Existing writer/registered-byte
tests now assert preservation, including terminal/expired/missing upload cases.
The finalizer still has a separate test proving it cannot revive an explicitly
rejected replica; the test no longer depends on unsafe scanner mutation.

CLI semantics:

- `--limit 1..100` limits each live scan page and pending query, **not total work
  in the invocation**. The same scanner advances until traversal exhaustion;
  restarting a first page on every batch would starve later directory entries.
- Default dry-run records required process control/exit receipts but creates no
  orphan claims and changes no bytes. It reports only positive observations.
- `--apply --drain-only` resumes durable orphan work without starting a scanner.
  Its `scan_complete` is false because it did not scan.
- Output remains aggregate-only. `scope=unregistered_cas` and
  `tracked_reconciliation_pending=true` explicitly limit coverage. `pass_complete`
  means this pass finished, not whole-Vault health or atomic snapshot completeness.
- Exit 0 means the requested pass finished without pending orphan claims; exit 5
  means pending/deferred ownership or a maintenance error, exit 4 invalid input,
  and exit 3 database failure. Interrupted work never emits a successful report.

Bounded independent design/implementation review is closed. It improved two tests:
the finalizer fixture first asserts original replica state; the pending-exit test
counts probe calls so a shutdown exception cannot mask an unwanted absence probe.
The reviewer made no edits and ran no tests.

Final evidence for sequential composition:

- Windows PostgreSQL: **59 distinct cases covered by passing results**. The
  five-file run passed 58 cases in 80.04 s before the added pending-exit regression;
  the final changed-module run passed 31 in 49.02 s, and all **18 orchestration
  cases** passed again after the probe-counter/explicit-generator-close changes
  in 39.25 s. An earlier run stopped at a test expecting no publication wait;
  it was corrected to observe the actual shared admission lock before commit.
- Windows CLI validation/application/CPU-entrypoint group: **24 passed in 14.64 s**.
- Linux final six-file group: **65 passed in 74.68 s**, comprising the same 59
  PostgreSQL cases and six CLI-validation cases. This includes a fresh actual
  admin CLI process and actual scanner/retirement children, without test mocks at
  the composition boundary.
- Ruff/format/strict mypy passed on all **10 affected Python files**;
  `git diff --check` passed. No migration was added; expected head remains 0045.
  Reported test sessions terminated, and scanner/storage shutdown checks left
  no pending local child.

The Linux proof uses the existing disposable acquisition image and PostgreSQL
container, with read-only mounts for `server`, `docs`, and `contracts` at their
`/repo/` paths. It shares only the disposable DB container's network namespace,
so the unchanged fixture safety guard reaches PostgreSQL at 127.0.0.1:5432.
`uv sync --frozen --no-install-project` installs the repository-locked environment
at `/opt/reconcile-venv` in the throwaway container; its source `.pth` points to
`/repo/server/src`. The first attempt used `/tmp/reconcile-venv` and failed before
test collection because `/tmp` was confirmed `noexec` in `/proc/mounts`, preventing
the psycopg extension from mapping. Moving the venv to the disposable container
layer fixed the setup; host dependencies, source, image tags, and DB guards were
not changed.

Reproduce the PostgreSQL group with the same Windows disposable URL and locked
pytest command used above:

```text
server/tests/postgresql/test_vault_reconciliation.py
server/tests/postgresql/test_vault_runtime.py
server/tests/postgresql/test_provider_staging.py
server/tests/postgresql/test_internet_publication_races.py
server/tests/postgresql/test_resource_upload_exclusion.py
```

The additional Linux/unit file is `server/tests/test_vault_reconciliation.py`.
The 24-case Windows group also includes `server/tests/test_admin_commands.py`,
`server/tests/test_vault_services.py`, and `server/tests/runtime/test_cpu_entrypoints.py`.

After repeated guessed-path search errors, consulted the official
[ripgrep guide](https://github.com/BurntSushi/ripgrep/blob/master/GUIDE.md).
Considered file discovery with `rg --files -g`, content-based discovery with
`rg -l`, and type-filtered recursive search with `rg -tpy`. Used explicit file
discovery before the subsequent reads rather than retrying assumed filenames.

# Upload staging creation behind durable execution

This local slice continues the full Admin/account objective after the sequential
reconciliation CLI. It closes the uncommitted HTTP upload's first-file window.
It does not complete the full goal or authorize production enablement, commit,
push, deployment or a production migration. No schema change was needed;
`0045_orphan_missing` remains the migration head. `.codex-state` was untouched.

## Implemented behavior

- `VaultUploadService.create` commits metadata without storage access. The HTTP
  service constructor no longer initializes the Vault root. A failed create
  transaction or lost commit reply cannot leave a staging file; an idempotent
  retry recovers the row in the usual way.
- The first admitted PATCH uses the existing exact RUNNING VAULT_UPLOAD execution
  and upload row lock. Its child receives the expected size from the locked row,
  validates all private protocol bounds and the payload hash, then initializes
  storage. Missing staging is creatable only at committed offset zero. Missing
  acknowledged bytes fail without recreating or resetting the upload.
- Zero-offset retry reconciles a prior dead child's uncommitted bytes. Directory
  fsync covers the staging entry, root's subdirectory entries and root's ancestor
  entries, including retries where a previous child died before those fsyncs.
  Windows retains the existing documented weaker directory durability; Linux is
  the deployment evidence for directory fsync.
- Before each new-byte write the child checks free space minus all remaining
  expected bytes against the configured reserve. The check precedes staging
  creation/truncation. Low capacity propagates as typed `vault_capacity_low` and
  retryable HTTP 507. It is a snapshot, not an aggregate disk reservation; an
  uncommitted suffix may conservatively cause refusal until capacity is restored.
- Exact child exit, the retained upload transaction, the post-write authority
  check and receipt commit remain mandatory. Duplicate receipt replay uses no
  storage operation or capacity probe. Ordinary non-expired completion is
  metadata-only; the ingest worker retains its own pre-start capacity check.
- The real HTTP adapter has no direct filesystem append fallback. If assembled
  without the coordinator it returns 503 `capability_missing` before reading a
  PATCH body. Legacy fake services remain injectable for isolated HTTP contracts.
- `VaultCapacityError` is now a pure `VaultError`, re-exported by the application
  module for compatibility. `ReconciledChunkWriter` includes expected size; the
  private parent/child GO command includes expected size and minimum free bytes.

## Ownership and failure evidence

`tests/postgresql/test_upload_staging_creation.py` covers actual POST/replay/HEAD,
first admitted PATCH, successful completion, no parent storage initialization,
committed upload plus RUNNING execution before GO, and exact exit acknowledgement
before the control receipt is collected. It also covers create and chunk commit
failure both before commit and after a lost successful reply, missing acknowledged
prefix, child 507, recovery after capacity returns and replay while capacity is low.

Existing upload-process tests now start without a staging file. Their revocation
and delayed-exit assertions therefore cover initial creation as well as append.
The inventory test now confirms that an uncommitted new HTTP upload creates no
file while legacy unknown staging remains protected. Existing append/stream and
writer exclusion cases remain in the regression group.

Child/filesystem cases cover no filesystem work before complete GO and payload,
zero-offset creation/retry, nonzero missing prefix, strict expected/reserve values,
full remaining-size watermark rather than just the current chunk, directory
publication after fresh/interrupted initialization, bad hash and unsafe staging
directory replacement. The API test proves a missing coordinator leaves the
request body unread.

## Independent review

The bounded read-only reviewer checked design and implementation. Findings about
durability of newly created directories and a reserve test that could not distinguish
whole remaining size from chunk size were fixed. Follow-up review is closed with
no actionable findings. The reviewer did not run tests or edit files.

## Validation

- Linux: **109 passed** in 96.51 seconds (56 PostgreSQL + 53 child/filesystem/
  application/API cases), with no skips. This final combined run includes the
  directory durability fix, actual symlinks and real isolated children.
- Windows: **69 passed, two symlink cases skipped** in 30.28 seconds in the final
  six-module unit/API/CPU group. Linux covers both unavailable host cases.
- Windows PostgreSQL: the seven-module regression passed **56** in 81.85 seconds;
  after the directory durability fix the affected three upload modules passed
  **16** again in 61.28 seconds. These are overlapping sets, not 72 distinct tests.
- Ruff, formatting and strict mypy passed on all **16** affected Python files.
  `git diff --check` passed; only pre-existing neighboring line-ending warnings
  were printed. All test sessions completed and the disposable Linux runner
  exited; Docker showed only the existing owned PostgreSQL container afterward.

Test databases are disposable `autplay_p02_*` databases on the owned PostgreSQL
18.4 container `autplay-admin-20260916-postgres-1`, host loopback port 1520. Linux
uses `autplay-acquisition:perf-gate-20260914`, read-only source/docs/contracts
mounts, a throwaway `/opt/reconcile-venv` from frozen `uv sync`, and the container's
database network namespace. Isolated children resolve source through a `.pth`
file; no production database, host environment or image tag is changed.

The PostgreSQL group is:

```text
test_upload_staging_creation.py
test_resource_upload_process.py
test_resource_upload_http.py
test_vault_inventory.py
test_resource_process.py
test_vault_io_coordinator.py
test_resource_upload_exclusion.py
```

The Windows unit/API group is `test_vault_child.py`, `test_vault_services.py`,
`test_vault_filesystem.py`, `runtime/test_vault_api.py`, `runtime/test_api.py` and
`runtime/test_cpu_entrypoints.py`. Linux combines the PostgreSQL group with the
first four unit/API modules, including actual symlink and isolated-process tests.
Use `uv run --project server --frozen python -m pytest -c server/pyproject.toml`,
`-q -x --tb=short` and `-W error::sqlalchemy.exc.SAWarning` for PostgreSQL evidence.

Two initial test-fixture corrections were necessary: the hash-mismatch payload
must still fit the declared expected size, and durable exit must be observed
before normal permit cleanup removes its short-lived execution receipt.
No production invariant was relaxed to make these tests pass.

Repeated missing/glob path errors were resolved using the official
[ripgrep guide](https://github.com/BurntSushi/ripgrep/blob/master/GUIDE.md):
available alternatives are `rg --files -g` discovery, `rg -l` symbol discovery,
and recursive `rg -tpy`/`-g` filtering. Discover paths first and use `-g` rather
than passing wildcard paths as literal PowerShell arguments.

## Remaining work

The follow-up below replaces direct cancel/expiry cleanup with durable claims and
the common retained maintenance process. Ingest verification/media/publication
and post-finalize staging cleanup still need exact process/tree ownership.

Unknown legacy staging remains observation-only. Tracked CAS integrity/missing
decisions must wait for the remaining writer protocols. Linux process-tree
containment, Android and target-device acceptance, account recovery/deletion,
training consent and measured production budgets remain part of the full goal.

## Follow-up: terminal device upload cleanup

Migration `0046_upload_cleanup` is the current local head. It adds immutable
`vault.upload_cleanup_claim` ownership and the `UPLOAD_CLEANUP` maintenance action.
Metadata now contains 151 tables, 1707 columns and 145 explicit indexes; fingerprint
`c83a1416272017f0812ada6cfcd57a02924fdad408c8a87074304d10f9e0857b`.
This is local implementation and disposable database evidence only.

Cancellation and expiry commit their canonical claim in the same transaction as
the terminal upload state. DELETE and expired HEAD/status/complete perform no
filesystem operation. Claim identity equals upload identity; the storage key and
terminal state cannot change. A completed claim remains replayable without touching
storage. Migration downgrade locks the ownership tables and refuses to discard
claims or maintenance history, including a claim committed while downgrade waits.

Only DEVICE uploads in CANCELLED/EXPIRED without CAS or provider/Internet lineage
are eligible. A global-admission then upload/claim lock order excludes every
unclosed VAULT_UPLOAD execution; a prepared writer racing a terminal intent still
blocks cleanup until acknowledged exit. The HTTP transaction takes upload then
claim and never acquires a nested global lock. SEALED cancellation and ingest
start serialize on the upload row. Actor identity cannot change, and PROCESSING
cannot rewind to OPEN/SEALED or either eligible terminal state. A terminal job or
expired lease provides no cleanup authority.

The retained common maintenance process moves one exact staging leaf to
`quarantine/upload-cleanup-<claim-id>`. Hard-link publication, directory fsync and
source unlink preserve present bytes; an unrelated existing destination is never
replaced. Safe root/staging/quarantine directories must already exist and retain
their identities. Missing namespaces defer work without creating directories.
Both exact leaves absent within that stable namespace may complete cleanup; the
receipt asserts no integrity, file-move count or history of previously present bytes.

Completion requires the exact CLOSED maintenance execution with PROCESS_EXIT,
exit code zero and retained child identity, plus no other unclosed execution for
the claim. A valid result frame alone is insufficient. Lost DB completion replies
replay the same claim and quarantine destination. Lost exit acknowledgement keeps
the shared maintenance singleton and claim pending. No TTL takeover is introduced.

`python -m autplay.entrypoints.admin vault-upload-cleanup --limit 100` drains a full
UUID-keyset pass, with each metadata query bounded to 1..100 rows. It also creates
claims for eligible historical terminal uploads. A closed storage failure defers
that claim and advances within the pass. Output contains only completed/deferred
claim counts and a pending flag. Exit code 5 indicates pending work or a maintenance
failure; 4 rejects invalid input and 3 reports unavailable database access. This
command does not activate a production scheduler.

### Review and fault evidence

Read-only independent review is closed. Findings fixed: immutable actor identity
prevents changing actor type to bypass state guards; claim timestamps use the DB
clock; pending selection uses all eligibility restrictions; stable namespace checks
follow the final leaf check. Actual lock barriers exposed a stale SQLAlchemy identity
map after a waited FOR UPDATE; the locked upload reads now refresh existing objects.
Both cancel and ingest winners are exercised with cached state.

New PostgreSQL cases cover HTTP metadata-only behavior, atomic intent rollback,
actual child GO after committed RUNNING ownership and with no checked-out parent
connection, exact exit completion, pre/post-commit lost replies, prepared upload
exclusion, both cancel/ingest races, SQL guard enforcement, poison-claim fairness,
historical expiry backfill, missing namespace, delayed exit acknowledgement,
competing maintenance, a fresh CLI process and downgrade contention. Filesystem
cases cover source/link/destination/absent replay, collisions, missing directories,
symlinks and directory replacement.

Two old regression fixtures attempted PROCESSING -> OPEN/SEALED rewinds. They now
create fresh rows in the required initial state. The ingest lease-expiry test still
waits on a real upload lock and proves rollback after lease expiry; its start case
now exercises the actual SEALED -> PROCESSING transition. No guard was relaxed.

After the repeated guard error, four fixes were evaluated from official sources:
query `populate_existing`, `Session.refresh(..., with_for_update=True)`,
`Session.get(..., populate_existing=True, with_for_update=True)`, and creating
separate valid initial-state fixtures. Existing SELECTs use the first option;
invalid fixture rewinds use the fourth. Sources: [SQLAlchemy query refresh](https://docs.sqlalchemy.org/en/20/orm/queryguide/api.html#populate-existing),
[Session refresh/get](https://docs.sqlalchemy.org/en/20/orm/session_api.html), and
[pytest fixture factories](https://docs.pytest.org/en/stable/how-to/fixtures.html#factories-as-fixtures).
Repeated E501 diagnostics were resolved by manually wrapping embedded SQL, following
[Ruff E501](https://docs.astral.sh/ruff/rules/line-too-long/) and
[formatter guidance](https://docs.astral.sh/ruff/formatter/); automatic formatting,
manual wrapping and configured exceptions were considered, with no lint relaxation.

### Follow-up validation

The adjacent Windows PostgreSQL schema/maintenance group passed **91** in 131.65
seconds: metadata, migration lifecycle/drift/privileges, all 46 adjacent migration
pairs, resource schema, provider maintenance/scratch, orphan missing, first upload
and upload exclusion. The new cleanup module passed **22** in 29.58 seconds before
the final cached-ingest/backfill strengthening; all 22 strengthened cases passed
again in the later combined run. Final ingest-fence regression passed **13** in
47.53 seconds after its fixture correction. These groups overlap.

Windows filesystem/child/application/API/CPU validation passed **37**, with three
host-unavailable symlink cases skipped, in 18.50 seconds. Its modules are
`test_upload_cleanup.py`, `test_provider_maintenance_child.py`,
`test_vault_services.py`, `runtime/test_vault_api.py` and
`runtime/test_cpu_entrypoints.py`.

Final Linux validation passed **103** in 249.29 seconds, with no skips (83
PostgreSQL and 20 filesystem/child cases). This run includes the corrected initial
fixtures, strengthened backfill/cached-state cases, actual child launches and all
three symlink cases unavailable on Windows. The PostgreSQL modules are:

```text
test_upload_cleanup.py
test_upload_staging_creation.py
test_resource_upload_process.py
test_resource_upload_http.py
test_resource_upload_exclusion.py
test_vault_runtime.py
test_vault_ingest_fence.py
test_provider_maintenance.py
```

The two Linux filesystem/child modules are `test_upload_cleanup.py` and
`test_provider_maintenance_child.py`. The disposable runner uses the frozen full
server dependency environment, read-only `/repo/server`, `/repo/docs` and
`/repo/contracts` mounts and the same PostgreSQL network namespace described
above. Use `-c /repo/server/pyproject.toml -p no:cacheprovider -q -x --tb=short
-W error::sqlalchemy.exc.SAWarning` with those paths. It creates isolated test
databases, not migrations in the base or production database.

Ruff, format and strict mypy passed on all **28** affected Python files. Ingest
ownership, tracked CAS reconciliation and the other full-goal requirements remain
open. The worktree remains uncommitted, including preserved adjacent changes.
`git diff --check` passed with only existing neighboring line-ending warnings.
All recorded test sessions terminated; the Linux runner exited and Docker showed
only the owned healthy PostgreSQL container. No matching filesystem child Python
process remained on Windows. No commit, push, deployment or production migration
was performed.

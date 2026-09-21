# Trusted offline execution drain: 2026-09-19

This local continuation closes the historical-restore gap without treating a lease timeout,
database age or missing supervisor memory as process-exit evidence. It changes no production
database and was not run with production credentials.

## Safety boundary

The operator must first stop every API, byte worker, metadata worker, training worker and
other process that can admit or launch work, and keep them stopped. The drain then acquires
the shared PostgreSQL resource-admission lock and locks every nonclosed resource, ingest,
ingest-cleanup, metadata, provider-maintenance and training execution row. It verifies all
persisted child identities before changing any row. A live or inaccessible process, invalid
PID, missing Linux containment root, symlinked containment root, unreadable cgroup evidence
or nonempty cgroup aborts the transaction, so no subset is closed.

On Windows the proof uses `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)` and
`GetExitCodeProcess`; `STILL_ACTIVE` blocks closure and only an invalid absent PID is accepted.
On Linux each persisted PID must be absent and every direct `autplay-*` group under the
configured delegated root must report exact `populated 0`. Root path components, groups and
event files are opened through pinned no-follow descriptors, and `fstatfs` must identify an
actual cgroup-v2 filesystem before an empty root is accepted. The canonical host
observation is hashed, and each closed row receives a domain-separated `SUPERVISOR_EXIT`
digest and exit code 137.

Ordinary resource, ingest, ingest-cleanup, metadata and maintenance executions become
closed in the same transaction. Provider staging receives its existing preserved exit
receipt, and the metadata singleton gate is cleared only for its exact execution. Training
in `PREPARED` or `RUNNING` moves through the authorized SQL transition to durable `STOPPING`,
records `retain_checkpoint=false` and keeps global capacity charged until the normal bounded
inventory cleanup commits `CLOSED`. Existing `STOPPING` training remains recoverable.

## Offline operator sequence

After restoring the database and independently retained consent/deletion ledgers, with all
normal supervisors still stopped, run:

```powershell
uv run --project gpu/training --frozen autplay-sona-training restore-drain `
  --input-root <exclusive-training-input-root> `
  --output-root <exclusive-training-output-root>
```

The command checks the exact schema readiness, atomically closes the restored reservations
and repeatedly completes training inventory cleanup. It is replay safe: a lost reply leaves
closed rows omitted on retry, while durable `STOPPING` training is still recovered.

The drain deliberately does not execute final account purge. First finish the cleanup work
created by the closed executions, using the normal bounded paths as applicable:

```powershell
uv run --project server --frozen autplay-admin vault-reconcile --apply --drain-only
uv run --project server --frozen autplay-admin vault-upload-cleanup
uv run --project server --frozen autplay-worker-cpu --once
uv run --project server --frozen python -m autplay.entrypoints.metadata_worker --once
```

Repeat only while those commands report bounded pending work. Then run the independent
restore guards, including:

```powershell
uv run --project server --frozen python -m autplay.entrypoints.training_consent_restore restore-guard
uv run --project server --frozen python -m autplay.entrypoints.privacy_admin restore-guard
```

Restart accepting processes only after both guards and the deployment's normal readiness
checks pass.

## Verification

Disposable PostgreSQL cases cover resource, ingest, ingest-cleanup, metadata,
provider-maintenance and training rows. They verify preserved provider evidence, metadata
gate release, retained cleanup claims, training `STOPPING` capacity retention and later
inventory cleanup. A real Windows child test proves that a live persisted PID aborts the
transaction and leaves the row `RUNNING`; a terminated child and an actually absent PID are
accepted. CLI routing for both cleanup recovery and `restore-drain` is covered. Ruff and
strict mypy pass on the affected source files.

The full `restore-drain` CLI composition also passed against a freshly migrated disposable
PostgreSQL database: one persisted absent Windows PID moved training through `STOPPING`, the
exclusive input inventory was removed, the execution became `CLOSED`, and the shared internal
slot returned to zero. This is a command-level integration check, not only parser routing.

Independent read-only review found that the first Linux implementation would accept an empty
ordinary directory as the configured containment root. The corrected implementation requires
cgroup-v2 by pinned descriptor as described above; a network-disabled, read-only Linux container
regression rejects an ordinary temporary directory. Follow-up review is `APPROVED` with no other
P0-P2 findings.

This is a trusted offline recovery protocol. It cannot prove that an unregistered process
outside the persisted identities or delegated Linux cgroup hierarchy is absent; stopping
and isolating all supervisors is therefore an explicit operator precondition. Physical
backup isolation, target-host process policy and a real restored-backup rehearsal remain
deployment acceptance work.

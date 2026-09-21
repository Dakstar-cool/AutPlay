# Account deletion implementation: 2026-09-18

Local implementation on `codex/readme-current-state`, based on
`860511aae9f1fcdb98b7b9b944f97ef27929fb8b`. No commit, publication, production migration
or real deletion was performed. The full Admin/account goal remains unfinished.

## Behavior

Migration `0052_account_deletion` adds a signed request with a 720-hour cancellation
window, immediate authority revocation, historical request lookup after bearer
revocation, and separate explicit cancellation through the current recovery code.
Cancellation rotates the code and creates exactly one fresh binding. Administrative
disable remains a veto. PostgreSQL forbids retiring the last ACTIVE nondeleted OWNER,
including concurrent transactions and stale REPEATABLE READ snapshots.

Migration `0053_privacy_purge` adds an explicit owner-data purge and narrow protected
exceptions for retained immutable evidence. Completed upload/ingest cleanup cycles,
reviewed import decisions, metadata revisions and retained recommendation inputs
can be erased. Running processes/jobs, incomplete cleanup and holds block deletion.
Shared canonical catalog data, Vault bytes and model artifacts survive; their
personal attribution is removed. Other users' invitation receipts and browser audit
references to deleted authority are included. UUID/text/JSON verification runs before
an immutable nonpersonal completion receipt is written.

Independent SQLite deletion evidence precedes the final database purge. The signed
chain/head/identity is verified on every operation; writes are serialized and use
SQLite EXTRA synchronization. Prepared evidence survives failure of the database
purge or the final acknowledgement. Completion capacity is reserved before accepting
another prepared record. The ledger currently retains evidence indefinitely.

Restore startup checks cover API, streaming, CPU/music/metadata workers and the
database-backed GPU worker. A real cloned PostgreSQL copy made before the deletion
request is used in the regression: the old ACTIVE account is removed before startup.
Missing/corrupt/wrong-key evidence fails closed, without automatic file creation.
Production settings require evidence configuration independently of feature flags.

## Operator configuration (not enabled here)

All processes must use the same independently retained local filesystem ledger and
key identity. Provision an absolute `AUTPLAY_PRIVACY_LEDGER_PATH` outside the Vault,
`AUTPLAY_PRIVACY_LEDGER_KEY_FILE` containing at least 32 bytes of secret UTF-8 key
material, and `AUTPLAY_PRIVACY_LEDGER_KEY_ID`. Do not place the ledger or its key in
the PostgreSQL restore set. Preserve and back up both independently with access
restricted to the trusted service/operator identity. The implementation does not
establish these storage/backup permissions or detect rollback of the ledger itself.

Current first provisioning requires a database at migration 0057 and no untracked
historical deletion requests. Stop every legacy accepting API/worker and drain all
their open accepting transactions first. Keep them stopped: a journal created
while an old acceptor remains live does not establish coverage. Then initialize:

```text
uv run --project server --frozen python -m autplay.entrypoints.privacy_admin initialize-ledger
```

The command verifies current PostgreSQL readiness and samples its `clock_timestamp()`
as immutable cutover C, after the operator has established that no old acceptance
can occur later. Host wall time is not the coverage clock. New requests at/before
C+120 seconds cannot be sealed as unaccepted; deletion readiness remains false
through C+240 seconds to cover a client's allowed 120-second timestamp lag. Android
shows initialization and does not detach a binding during that window.

This refuses an existing file. Legacy files without the new signed coverage and
request chain fail closed; there is no automatic upgrade, adoption or backfill.
Do not replace such a file or set an earlier C to bypass historical requests.
Never replace lost history: recover the original ledger/key and keep the service
offline until independently retained evidence is verified. A preserving offline
upgrade of a legacy deployment remains separate operator work. See
[the current resolution slice](ADMIN_DELETION_RESOLUTION_2026_09_19.md).
After an approved migration and isolated restore, the explicit gate is:

```text
uv run --project server --frozen python -m autplay.entrypoints.privacy_admin restore-guard
```

`purge-due --limit 10` runs the gate and a bounded mature-request batch. Normal CPU
workers also support `AUTPLAY_ACCOUNT_PURGE_ENABLED=true` (default false), including
`--once`. Long-running scheduling cycles past held/blocked owners and reconciles lost
external completion writes. API request enablement requires
`AUTPLAY_ACCOUNT_DELETION_ENABLED=true`, enabled account recovery, and persistent
server identity. These switches were not enabled on a deployment.

## Remaining acceptance gaps

- An old backup containing open process reservations or staging still blocks startup until
  the trusted [offline execution drain](ADMIN_OFFLINE_EXECUTION_DRAIN_2026_09_19.md) proves
  persisted processes absent, atomically closes their reservations and normal bounded
  cleanup drains the resulting claims. TTL is never accepted as exit proof. A real isolated
  backup rehearsal and target-host process policy remain deployment acceptance work.
- Android request/cancellation UI, encrypted retry state and exact negative resolution
  are implemented in the subsequent linked verification records; real device/server
  acceptance remains open.
- Shared-training consent and dataset/artifact revocation remain a separate required
  block. Preserving shared audio/model metadata is not evidence of completing that
  privacy workflow.
- Physical Windows Hello/A55, private-network checks, real resource measurements and
  full end-to-end acceptance remain open. Windows power-loss storage durability and
  production backup retention have not been measured by these local tests. Use the
  [target acceptance runbook](ADMIN_TARGET_ACCEPTANCE_2026_09_19.md).
- A restored snapshot where the deleted owner is the only effective OWNER remains
  fail-closed under the user-approved last-OWNER rule; there is no auto-promotion.

## Verification

Disposable PostgreSQL 18.4 uses isolated `autplay_p02_*` databases; no production data
is used. SQL and orchestration received bounded independent read-only reviews.
All earlier reported code findings are closed. The later offline-drain record supersedes
the historical implementation gap above; reviewers inspect code and tests but do not run
the implementation agent's checks.

- Combined server run: **131 passed in 58.32s**. Includes 15 final-purge cases,
  suspension/cancellation/recovery HTTP and PostgreSQL regressions, 11 ledger cases
  (including two independent Python processes), eight startup/configuration cases,
  runtime settings, metadata inventory/fingerprint, live Alembic drift and clean
  upgrade/downgrade/upgrade of all 53 migrations.
- Separate PUBLIC-privilege check: **1 passed**. Separate root contract/schema/proof
  vector check in the root environment: **1 passed**.
- GPU database-worker startup guard: **1 passed** using CPU environment and synthetic
  inventory, before accelerator runtime imports. This is not a GPU execution test.
- Ruff/format checks passed on 27 affected Python files; strict mypy passed on 25
  server implementation/test files. `git diff --check` passed (existing line-ending
  warnings remain in adjacent work). No Android code changed in this server slice.

The combined command used `uv run --project server --frozen python -m pytest` with
`server/tests/test_deletion_ledger.py`, `server/tests/test_account_deletion_document.py`,
`server/tests/runtime/test_privacy_startup.py`, `server/tests/runtime/test_settings.py`,
the PostgreSQL `test_privacy_purge.py`, `test_account_deletion.py`,
`test_account_deletion_http.py`, `test_account_recovery.py`,
`test_account_recovery_http.py`, `test_metadata.py`,
`test_migration_close_gates.py::test_live_alembic_metadata_check_has_no_upgrade_operations`
and `test_migrations.py::test_clean_upgrade_downgrade_and_upgrade_again`.
The root contract check uses the root locked environment, where JSON Schema tools
are declared. No dependency changes were made to accommodate these checks.

Debugging decisions: repeated guessed paths were replaced by discovery with
[`rg --files` and directory/glob searches](https://github.com/BurntSushi/ripgrep/blob/master/GUIDE.md).
Windows console-script trampoline failures use
[`uv run ... python -m ...`](https://docs.astral.sh/uv/concepts/projects/run/) rather
than reinstalling the environment or changing pinned dependencies. Social receipts
store JSON as text; field predicates use an explicit JSONB cast as documented by
[PostgreSQL](https://www.postgresql.org/docs/18/functions-json.html).

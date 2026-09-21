# Independent training-consent restore fence and power-loss recovery

Local continuation of the full Admin/account goal after the laptop lost power.
This extends [the execution slice](ADMIN_TRAINING_EXECUTION_2026_09_19.md); it does
not enable production training or close the full acceptance goal.

## Power-loss audit

The saved implementation, release records and Git objects survived. Git connectivity,
`git diff --check`, source compilation and the six metadata checks passed. Both saved
Android APKs passed ZIP CRC checks. Saved deletion JVM reports retain 62 and 34 passes;
the latest consent reports retain 15 runtime and one transport pass, without failures.
These are recovered reports, not new Android test executions.

The owned disposable PostgreSQL container stopped with exit 255, without an OOM flag.
It was restarted and PostgreSQL completed automatic WAL recovery/checkpoint and became
healthy. Its dynamically assigned loopback port changed; checks use the discovered
current port. `pg_amcheck --install-missing` passed on the disposable base database and
the saved fixture database (155 application/extension tables). Installing amcheck is
confined to these disposable databases. The existing fixture was preserved.

The recovered cluster also passed the three current-schema real PostgreSQL -> CPU/ONNX
-> consumer cases in 38.41 seconds before the new restore implementation. This does
not attest the physical disk, production backups or target-device acceptance.

## Implemented independent authority

`FilesystemTrainingConsentLedger` stores every validated grant/refusal/withdrawal
intent before PostgreSQL mutation/commit. Its separate SQLite file uses EXTRA
durability, DELETE journal mode, bounded lock waits, signed identity, HMAC chained
events and a signed head. Runtime opening never creates or repairs absent history.
There is no TTL, reset or deletion operation. Accounts/devices are purpose-separated
HMAC tags; operation UUIDs are globally unique, with exact immutable bindings.

Independent sequence, rather than PostgreSQL revision alone, identifies each owner's
latest decision. A new-work grant requires full agreement between that latest intent,
the immutable PostgreSQL operation receipt and current policy: owner, actor, canonical
request hash, decision, revision, policy version and original timestamp. This prevents
an alternate restore branch with the same GRANTED revision from supplying authority.
Checks read evidence after acquiring all relevant account/policy locks. Registration,
start, progress/input/candidate checks, checkpoint seal and publication require it.

A failed PostgreSQL commit retains the external intent and conservatively blocks work.
Exact pending retry pins its previous policy hash, latest-owner position, original
revision/time and current authenticated actor. An older superseded pending operation
returns restore attention, without claiming a negative historical result. Exact old
receipts cannot return a current GRANTED policy without its latest independent proof.

Restore reconciliation records a system PRIVATE barrier where needed, then uses the
existing ordinary +1 private policy update; no revision bypass or new migration is
needed. An already-retained private barrier is reused if the PostgreSQL commit fails.
Already-private/terminal policies still invoke contribution invalidation/cleanup.
All affected run rows in a batch are locked as a complete sorted union before callbacks.
System reconciliation never fabricates a user's operation receipt. Each latest grant,
including a pending grant, reserves one future private event before admission to the
bounded journal, so unrelated/redundant commands cannot consume its withdrawal slot.

Startup conservatively privatizes an unmatched pending grant as well as a restored old
grant. Consequently an uncommitted grant may require another explicit choice after a
restart. This favors privacy; it does not report that the original operation succeeded.
Already PUBLISHED models and exact publication replay retain the agreed behavior after
ordinary withdrawal, without requiring live consent evidence. Input cleanup claims stay
pending; no process exit, released capacity or completed cleanup is inferred.

## Composition and provisioning

Settings accept explicit `training_consent_ledger_path`, `training_consent_ledger_key`
and `training_consent_ledger_key_id`, with matching `AUTPLAY_TRAINING_CONSENT_LEDGER_*`
environment fields and existing secret-file loading. The file must be absolute, outside
the Vault and separate from the deletion ledger; its key must also be separate.
Production processes require this configuration even when the feature is disabled.
Other profiles require it when enabling consent. Production enablement still rejects
the feature until controlled training execution/cleanup is verified.

Explicit offline provisioning:

```powershell
uv run --project server --frozen python -m autplay.entrypoints.training_consent_restore initialize-ledger
uv run --project server --frozen python -m autplay.entrypoints.training_consent_restore restore-guard
```

The normal API/stream/CPU/music/metadata/GPU-job startup guard now includes consent
reconciliation after deletion reconciliation. Missing, corrupt or wrongly keyed evidence
blocks startup and new authority; HTTP reports a private 503. Low-level training authority
requires an explicit ledger port. These commands were tested only against disposable
fixtures; no real journal/key or production data was provisioned.

The file and key must be retained independently of PostgreSQL backup generations.
Signed chains detect modification/truncation, not replacement with an entire authentic
older file. Joint rollback of that independent store is outside this protection. Real
storage durability, backup procedures and performance at maximum history remain operator
acceptance work; local SQLite tests are not those measurements.

## Verification and remaining scope

Final affected server batch: **134 passes in 66.13 seconds**, covering policy, registry,
restore clones/races, independent journal integrity/capacity, HTTP, startup and settings.
Earlier targeted passes are included in this count, not added again.

The new real-database cases use closed, guarded `autplay_p02_*` database clones, including
same-revision alternate branches, failed private commit, old receipt replay, pending grant
startup, terminal policy and published serving/replay. The waiting-batch test proves a
fresh ledger read after account-lock wait/rollback. A separate two-run NOWAIT test proves
the guard holds its full affected-run union before its first policy mutation; it is not
a reproduction of every 256-account deadlock schedule.

After the ledger changes, the real PostgreSQL -> CPU/ONNX -> consumer suite passed
three cases in 41.74 seconds with three existing Torch export warnings. Final GPU startup
checks passed two cases in 1.75 seconds. Strict mypy passed on 18 affected server files
and three current-source training files; Ruff/format checks passed. A broader GPU
entrypoint typing attempt exposed six errors in adjacent dirty `application/sync.py`
(SQL predicate/nullability/payload/generic return typing). The subsequent
[continuation slice](ADMIN_DELETION_RESOLUTION_2026_09_19.md) fixes those errors and
the real PostgreSQL catalog metadata query budget. The broader GPU entrypoint check
then passed; existing adjacent work is preserved. Fixture export typing found in
the earlier attempt was corrected using the actual readiness module import.

Both non-editable GPU/training environments initially still held the previous local
server wheel. `uv sync --frozen --reinstall-package autplay-server` rebuilt/reinstalled
only that local package in each environment without changing pins/locks. Runtime imports
then verified seven installed server modules against current-source SHA-256 in each
environment, without pytest's source-path override. The GPU entrypoint module import
also passed. This is local dependency refresh, not production deployment.

Independent read-only review is closed after fixing the run-lock order and withdrawal
capacity reserve findings. Reviewers read code/tests but did not execute checks.

The subsequent execution continuation introduced **0059_training_publication_seal**. The later
local-bridge closure advances the current head to **0060_local_bridge_authority**, still with
**170 tables, 1921 columns and 156 explicit indexes**; current mapping fingerprint
`f3f1f8db5c44bb46d1f8d07a6df85618b6b4af04fa568d2933696f327b893215`.
Branch/HEAD remain `codex/readme-current-state` /
`860511aae9f1fcdb98b7b9b944f97ef27929fb8b`; changes are local/uncommitted and extensive
adjacent work is preserved. No commit, push, deployment or production migration occurred.

Controlled owner-data preparation, byte/process admission, training-root inventory and
cleanup with exact writer-exit proof, and serving CLI composition are implemented in the
subsequent execution continuation. A trusted restored-process closure path is described in
[offline execution drain](ADMIN_OFFLINE_EXECUTION_DRAIN_2026_09_19.md). Windows Hello,
A55, private-network exclusion, joint target resource measurements and an operator rehearsal
on a real isolated backup remain open. The ordinary consent restore fence implemented here
does not substitute for those physical acceptance steps.
Expired unaccepted-deletion resolution is implemented in the subsequent linked slice.

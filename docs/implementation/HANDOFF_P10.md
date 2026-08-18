# P10 Handoff - Import, Identity and Offline Review

## Outcome

P10 is complete. User-owned CSV, JSON and HTML imports are bounded, resumable and auditable;
identity evaluation is explainable and shadow-only; explicit manual review is available offline;
fingerprint/version provenance and reversible catalog-change history are executable. A-023 through
A-025 are PASS. No P11 implementation was started.

The P09 prerequisite, accepted ADR-019 and the current design/schema inputs were verified before
implementation. ADR-023 records the final import/identity/review boundary. The final independent
read-only review reports no remaining Critical or Major issue after all fix loops.

## Delivered scope

### Server

- Versioned bounded CSV/JSON/HTML parsing supports UTF-8 and CP1251, stable row keys, raw/unknown
  field retention, row-level rejection and deterministic input SHA-256 identity.
- Capability/limit/auth/rights manifests define generic export and provider-neutral metadata
  adapter ports. P10 selects no live provider, stores no credentials in provenance and fetches no
  private resources.
- `library.import/v1` uses the existing CPU worker and durable job tables. Import jobs have bounded
  phase checkpoints, safe cancellation/resume, replay-stable IDs, row isolation and sorted reports.
- Candidate generation unions metadata, exact identifier and compatible fingerprint evidence,
  retains origins/version markers/hard conflicts, and emits bounded explanations plus top-two
  score/margin evidence.
- Every T0-T4 matcher evaluation is immutable `SHADOW` evidence and changes no ImportEntry,
  UserTrackRef or catalog projection. F-016 activation remains disabled and no policy activation
  event is written.
- Manual accept/create atomically appends separate typed ImportEntry and UserTrackRef evaluation
  and review lineages. Duplicate rows and multiple playlist intents share one active resolved
  owner mapping while preserving distinct immutable query histories.
- Catalog MERGE/SPLIT/REASSIGN/UNDO remains an explicit authorized command. Stable ordered
  Recording locks, inbound-dependency rechecks, atomic apply and inverse UNDO provenance prevent
  partial or chained redirects.
- Benchmark reports bind `dataset_id`, version and RFC 8785 canonical corpus SHA-256 to confusion,
  precision/recall, hard-negative and error slices. Fixture evidence cannot activate auto-match.

### Android

- Room advances additively from v7 to v8 with profile-scoped local import job/entry, append-only
  match decision/candidate and durable local review/control outbox state.
- Local evaluation is a separate shadow lineage. Accept, reject, keep-unresolved and create-recording
  actions update review/projection/outbox state in one Room transaction; global merge is not exposed.
- Pause/cancel are terminal mutation barriers until explicit resume. Restart, rollback, duplicate
  row keys and bounded evidence/report documents are covered.
- SAF/content URI inspection preserves missing, revoked and generic I/O intent. Verified content
  SHA remains evidence while import identity also includes the source URI, so equal bytes from two
  distinct source intents do not collide.
- The import/review Compose flow remains local-first and does not require a synchronous server call.

## Decisions

- ADR-023 is accepted under the standing in-scope technical authorization.
- PostgreSQL Alembic remains at `0012_sync_runtime`: the existing import, identity, jobs and audit
  tables satisfy P10, so no duplicate `0013` schema was added.
- Room v8 is additive and has no destructive fallback.
- A live public metadata provider and its legal/rate/auth policy remain an explicit future decision;
  P10 provides only the bounded provider-neutral seam and local/generic inputs.
- P10 review/control outbox rows are durable local intent. End-to-end server delivery requires a
  later additive sync contract and does not overload the frozen P04 event vocabulary.

## Migrations and contracts

- Alembic head: `0012_sync_runtime`; inventory remains 62 tables, 59 explicit indexes, 13 helper/
  constraint functions and 41 non-internal triggers; autogenerate drift is zero.
- Room head: v8 with named `MIGRATION_7_8` in addition to every prior supported migration.
- Room v8 normalized schema SHA-256:
  `7639eb1f005957e057a76812ec4a1a7a2699ed5c451443b4883dda309d73f82c`.
- P04 schemas remain frozen. No new network sync event is claimed by P10.

## Principal implementation and evidence paths

- Server domain/ports/application:
  - `server/src/autplay/domain/import_identity.py`
  - `server/src/autplay/ports/source_adapters.py`
  - `server/src/autplay/application/imports.py`
  - `server/src/autplay/application/catalog_changes.py`
- Server adapters/API/worker:
  - `server/src/autplay/adapters/postgresql/import_runtime.py`
  - `server/src/autplay/adapters/postgresql/catalog_changes.py`
  - `server/src/autplay/entrypoints/import_http.py`
  - `server/src/autplay/entrypoints/worker_cpu.py`
- Server evidence:
  - `server/tests/test_import_identity.py`
  - `server/tests/runtime/test_import_api.py`
  - `server/tests/postgresql/test_import_identity_runtime.py`
  - `tests/fixtures/import/`
- Android runtime/evidence:
  - `apps/android/src/main/kotlin/app/autplay/application/importing/LocalImportReviewRepository.kt`
  - `apps/android/src/main/kotlin/app/autplay/application/importing/ContentUriInspector.kt`
  - `apps/android/src/main/kotlin/app/autplay/data/local/AutPlayDatabase.kt`
  - `apps/android/src/androidTest/kotlin/app/autplay/application/importing/LocalImportReviewRepositoryTest.kt`
  - `apps/android/src/androidTest/kotlin/app/autplay/data/local/P10RoomMigrationTest.kt`
  - `apps/android/src/androidTest/kotlin/app/autplay/OfflineLibraryScreenTest.kt`
  - `apps/android/schemas/app.autplay.data.local.AutPlayDatabase/8.json`

## Acceptance evidence

| Acceptance | Executable evidence | Result |
| --- | --- | --- |
| A-023 resumable/auditable import | Golden CSV/JSON/HTML parsing; deterministic replay, checkpoint/cancel/resume/report real-PG tests; Room restart and source-intent tests | PASS |
| A-024 no silent ambiguous merge | Hard-negative/tie/version-marker benchmark; T0-T4 shadow/no-projection assertions; explicit review lineages; concurrent catalog apply tests | PASS |
| A-025 fingerprint provenance | Algorithm/version/origin candidate union, incompatible-version behavior, immutable history and duplicate-query reprocessing tests | PASS |

## Exact verification commands and results

All commands ran from the repository root on Windows with Microsoft OpenJDK `17.0.20+8-LTS`,
Android SDK 36.1/Build Tools 36.1.0 and disposable PostgreSQL 18.4 + pgvector 0.8.6.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\check.ps1
```

PASS: frozen bootstrap/locks, 80 harness tests, 51 P04 contract tests, server import/Ruff/format/
strict-mypy/CPU audit, Android lint, 40 host tests, debug and minified release/R8 builds, exact
PostgreSQL gates and the complete server suite: 386 passed, 1 skipped in 164.40s. The single skip
is the declared Windows symlink-privilege case. All scoped PostgreSQL resources were removed.

```powershell
$env:ANDROID_SERIAL='emulator-5584'
.\gradlew.bat "-Dorg.gradle.java.home=$env:JAVA_HOME" --no-daemon --console=plain `
  :apps:android:connectedDebugAndroidTest
```

PASS on disposable Android 8.0/API 26 AVD `autplay_p10_api26`: 69/69 tests, zero failures and zero
skips, `BUILD SUCCESSFUL in 1m 8s`. This includes all prior device evidence plus P10 repository/lifecycle/shadow/URI, v7-v8
migration and import UI tests.

Focused final server gates also passed Ruff format/check, strict mypy, 30 pure/runtime/API tests and
9 real-PostgreSQL import/identity/catalog tests. The final affected playback URI/process regression
passed 4/4 device tests before the complete connected run.

## Repeated-error protocol

- A repeated SQLAlchemy parent/child FK ordering failure was researched against official Session
  flush/unit-of-work guidance. Explicit parent-layer flushes were selected over broad relationship
  remapping, `no_autoflush` or query reordering, preserving one atomic transaction.
- Repeated Ruff I001 failures were resolved with Ruff's documented targeted `check --select I
  --fix` workflow under the exact canonical server config, followed by format/check.
- The full device gate twice exposed readable URI state as missing. Official Android provider and
  instrumentation documentation confirmed that the exported provider and target/test contexts
  were valid. The root cause was local: successful non-digest inspection returned nullable digest
  `null` through an Elvis expression and was misclassified as a missing stream. Separating stream
  presence from optional digest fixed the resolver and playback checkpoint tests.

## Independent review

Independent architecture, server, Android and final integrated reviews covered shadow/application
boundaries, typed decision ownership, duplicate imports, fingerprint/identifier shortlist behavior,
idempotency races, chunked request bounds, catalog concurrency/undo provenance, Room lifecycle,
URI identity and offline atomicity. Every Critical/Major finding was fixed with regression evidence.
The final re-review reports no remaining Critical or Major issue.

## Not delivered / future ownership

- P11 recommendation generation/evaluation and P12 GPU enrichment were not started.
- No live external metadata provider, credential flow, private scraping or acquisition adapter was
  selected or enabled.
- Automatic probabilistic identity activation remains disabled pending the complete production
  F-016 benchmark/sample-size/rollback gate.
- End-to-end transport of P10 local review/control intent remains a future additive sync contract.
- P13 Wave and P14 production TLS, backup/restore, security soak, physical Samsung A55 and release
  evidence remain future-phase work.

## Risks and debt

- No known P10 product blocker remains. Overlapping catalog plans deliberately conflict and must be
  explicitly re-proposed after the earlier reviewed change commits.
- Production provider/legal selection, auto-match activation, review sync transport, scale soak,
  backup/restore and physical-device qualification retain their documented future owners.

## Repository and cleanup state

- Branch: `codex/autplay-harness-v1`.
- HEAD observed at handoff: `0023fa9ad9d12633ad988230662fbd69bb74eb20`.
- P04-P10 and harness work remains intentionally uncommitted in the shared dirty worktree; unrelated
  user changes were preserved and no commit, push, PR, publish or deployment was performed.
- No P10 Docker resource remains. The disposable API 26 emulator and registered AVD were removed
  after the final connected gate.

## Exact next prerequisite

P11 is eligible but not started. Continue only with
`docs/build-pack/prompts/P11_recommendations_cpu.md` after reading this handoff, ADR-017, ADR-023 and
the recommendation subsystem design. Do not treat P10 benchmark evidence as permission to activate
automatic identity matching.

# P09 Handoff - End-to-End Sync Engine

## Outcome

P09 is complete. The P04 Sync Protocol v1 now has executable server and Android engines for
offline Journal push, durable per-event ACK, opaque-cursor pull, conflicts, tombstones,
bootstrap/reset and status/retry. A-018 through A-022 are PASS. No P10 implementation was started.

The P08 prerequisite was verified from `HANDOFF_P08.md`, its accepted ADR-021, the frozen P04
contracts/vectors and the canonical repository gates before P09 work began. The final independent
review found no remaining Critical or Major P09 issue after the documented fix loops.

## Delivered scope

### Server runtime

- Authenticated `/devices/bind`, `/sync/push`, `/sync/pull`, `/sync/bootstrap` and `/sync/status`
  routes are wired into the CPU-only modular monolith.
- Push validates bounded generic and known-event payloads, canonical hashes and device sequence
  integrity. It serializes a device lineage and durably commits each eligible event independently,
  including inbox reservation, owner/version checks, domain or interaction projection, canonical
  sync event, terminal ACK/idempotency result and device checkpoint.
- Exact retries return the stored terminal result without a second mutation. Same-ID/different-hash,
  event/sequence/idempotency mismatches, gaps and reordering fail with stable precedence.
- P07 library, track, playlist and playlist-entry create/update/move/delete payloads are projected in
  the same server session. Destructive/overwrite operations use the observed base row version; no
  timestamp last-write-wins path exists.
- Listening, recommendation impression and feedback events have specialized schema dispatch,
  same-owner request/rank/recording validation, ordered same-device causal validation and
  owner-scoped presentation uniqueness. Canonical interaction facts are append-only and exactly
  once.
- Pull cursors are signed, opaque, expiring and bound to protocol, owner, device, Journal epoch,
  cursor generation and checkpoint. The supplied cursor is acknowledged only when presented.
- Bootstrap materializes one authoritative owner snapshot at a fixed high-water mark, including
  retained tombstones and recording redirects. Page tokens are signed and bound to the exact
  snapshot; continuation never reads a changing live projection.
- Deletes emit a soft-delete/domain mutation, canonical sync event and retained tombstone in one
  transaction. Compaction requires expiry plus every non-revoked active device checkpoint to pass
  the delete sequence.
- `CURSOR_INVALID` returns HTTP 410 and `DEVICE_RESET_REQUIRED` returns HTTP 409 with an explicit
  bootstrap directive. Metrics/logging retain bounded identifiers and classifications, never event
  payloads, tokens or private URLs.

### Android runtime

- A profile-scoped coordinator and OkHttp `5.4.0` transport implement bind, bounded push/ACK, pull,
  bootstrap and status. WorkManager input contains only device/profile identifiers, requires a
  connected network and uses bounded exponential retry.
- Journal leasing never skips the lowest nonterminal sequence. Transport failure recovers the lease
  for the same immutable IDs/hashes. ACK preflight validates the complete binding and server
  identity/version before any Room mutation.
- ACK apply, pull page apply and bootstrap page apply are transactional. Unknown, malformed,
  reordered or incomplete server data is deferred/fails closed without cursor advance.
- Clean rows receive typed projection updates. Dirty rows are never overwritten and instead retain
  deterministic profile-scoped conflict evidence. Parentless deletes retain tombstones without
  creating live aggregates; recording redirects update only the profile-scoped redirect mapping.
- Bootstrap uses separate snapshot/page state, preserves every pending Journal event byte-for-byte,
  handles multiple pages idempotently and installs the snapshot cursor only at final cutover.
- A run drains at most ten pull/bootstrap pages. Remaining work stores `PULL_CAP_REACHED` and returns
  WorkManager retry rather than claiming success.
- Sync Status UI exposes pending, conflict, dead-letter, last success/error and manual retry.
  Bound mutations schedule sync only after the local transaction commits.
- Room v7 scopes all six synchronized domain projections, conflicts, tombstones, redirects,
  interaction facts, status and search/list queries by profile. Standalone data uses the explicit
  `legacy-unscoped` owner and is claimed atomically during authenticated materialization.

## Decisions

- ADR-022 is accepted under the standing in-scope technical-decision authorization. It records the
  per-event server transaction, opaque cursor, materialized bootstrap, tombstone, WorkManager and
  pre-ACK destructive-intent policy.
- A dependent destructive/edit event created before its create ACK has no truthful observed server
  version. The immutable event is preserved as visible `CONFLICT/POLICY_REVIEW`; it is never
  rewritten, discarded or granted implicit last-write-wins authority.
- PostgreSQL remains canonical, Room remains local-first, and the implementation adds no broker,
  cache service, sync microservice or payload-bearing WorkManager state.

## Migrations and contracts

- Alembic head: `0012_sync_runtime`, additive from `0011_vault_runtime`.
- Exact PostgreSQL inventory: 62 tables, 59 explicit indexes, 13 helper/constraint functions and 41
  non-internal triggers. The two reference SQL sources remain byte-identical and metadata drift is
  zero under the declared physical-only exclusions.
- `0012` extends the durable inbox/cursor lineage and terminal result state and adds materialized
  bootstrap rows plus canonical user-interaction facts.
- Room head: v7 with named migrations `MIGRATION_1_2` through `MIGRATION_6_7`; no destructive
  fallback. P09 adds runtime status, independent bootstrap state, profile-scoped conflict/tombstone
  state, recommendation facts and profile ownership for synchronized projections.
- Room v7 normalized schema SHA-256:
  `ff44bce40b9934784d9022e7eee8ada7ac86fee34624dd8e7be2ac91d93a0b9d`.
- P04 JSON Schemas, OpenAPI and RFC 8785 golden vectors remain frozen; the P09 engines consume them
  without changing their normative semantics.

## Principal implementation and evidence paths

- Server migration and runtime:
  - `server/migrations/versions/0012_sync_runtime.py`
  - `server/src/autplay/application/sync.py`
  - `server/src/autplay/entrypoints/sync_http.py`
  - `server/src/autplay/adapters/postgresql/models/sync.py`
- Server evidence:
  - `server/tests/test_sync.py`
  - `server/tests/postgresql/test_sync_runtime.py`
  - `server/tests/runtime/test_api.py`
- Android runtime:
  - `apps/android/src/main/kotlin/app/autplay/application/sync/SyncEngine.kt`
  - `apps/android/src/main/kotlin/app/autplay/application/sync/OkHttpSyncTransport.kt`
  - `apps/android/src/main/kotlin/app/autplay/work/SyncWorker.kt`
  - `apps/android/src/main/kotlin/app/autplay/data/local/AutPlayDatabase.kt`
  - `apps/android/src/main/kotlin/app/autplay/MainActivity.kt`
- Android evidence:
  - `apps/android/src/androidTest/kotlin/app/autplay/application/sync/SyncCoordinatorAcceptanceTest.kt`
  - `apps/android/src/androidTest/kotlin/app/autplay/data/local/P09RoomMigrationTest.kt`
  - `apps/android/schemas/app.autplay.data.local.AutPlayDatabase/7.json`
- Decision and acceptance records:
  - `docs/adr/ADR-022-p09-sync-runtime-transaction-and-reset.md`
  - `docs/build-pack/MVP_ACCEPTANCE_MATRIX.md`
  - `docs/implementation/TRACEABILITY.md`

## Acceptance evidence

| Acceptance | Executable evidence | Result |
| --- | --- | --- |
| A-018 push idempotency | P04 duplicate/hash vectors; real-PG exact replay/changed-hash/lost-response/device serialization and interaction semantic uniqueness; Android duplicate outcome handling | PASS |
| A-019 atomic cursor | Signed cursor owner/epoch/generation tests; Android ACK/page preflight, unknown/malformed/reordered/incomplete page rollback and bounded-drain retry | PASS |
| A-020 tombstone lifecycle | Real-PG delete/bootstrap and lagging-active-device compaction test; Android parentless delete and bootstrap tombstone projection | PASS |
| A-021 dirty conflict | Android dirty edit versus remote delete, deterministic retry conflict and two-profile same-server-ID isolation | PASS |
| A-022 reset preserves intent | Real-PG fixed bootstrap snapshot/token substitution tests; Android invalid cursor, pending Journal, multi-page cutover and v2-v7 migration preservation | PASS |

Additional P09 evidence covers same-owner recommendation/listening success, missing and cross-owner
attribution indistinguishability, causal feedback, same-presentation dedupe, unsupported version,
profile-scoped FTS/UI, 100-event leasing and process-safe resume.

## Exact verification commands and results

All commands ran from the repository root on Windows with Microsoft OpenJDK `17.0.20+8-LTS`,
Android SDK 36.1/Build Tools 36.1.0 and disposable PostgreSQL 18.4 + pgvector 0.8.6.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\check.ps1
```

PASS: frozen bootstrap/locks, 80 harness tests, 51 P04 contract tests, server import/Ruff/format/
strict-mypy/CPU audit, Android host lint/unit/debug/minified-release, exact PostgreSQL migration and
catalog gates, and the complete server suite: 362 passed, 1 skipped. The single skip is the declared
Windows symlink-privilege case, not a P09 behavior skip. The scoped Compose resources were removed.

```powershell
$env:JAVA_HOME='C:\Users\ptica\AppData\Local\Temp\autplay-jdk-17.0.20-8\extracted\jdk-17.0.20+8'
.\gradlew.bat "-Dorg.gradle.java.home=$env:JAVA_HOME" --no-daemon --console=plain `
  :apps:android:lintDebug :apps:android:testDebugUnitTest `
  :apps:android:assembleDebug :apps:android:assembleRelease
```

Final post-fix PASS: `BUILD SUCCESSFUL`; lint reports `No issues found`; 36 host tests pass; debug and
minified release/R8 APKs are produced.

```powershell
$env:ANDROID_SERIAL='emulator-5582'
.\gradlew.bat "-Dorg.gradle.java.home=$env:JAVA_HOME" --no-daemon --console=plain `
  :apps:android:connectedDebugAndroidTest
```

Final post-fix PASS on disposable Android 8.0/API 26 AVD `autplay_p09_api26`: 59/59 tests, zero
failures and zero skips, `BUILD SUCCESSFUL in 58s`.

```powershell
cd server
uv run pytest tests/test_sync.py tests/postgresql/test_sync_runtime.py -q
```

Focused final server sync PASS against disposable PostgreSQL: 16 passed in 13.08s. The broader
focused sync/runtime set passed 26 tests, and the complete canonical run above subsumes both.

## Independent review

Read-only architecture and separate server/Android review cycles covered transaction boundaries,
idempotency precedence, bootstrap authority/token binding, interaction attribution, wire shapes,
cursor HTTP semantics, ACK preflight, dirty/delete behavior, profile isolation, FTS visibility,
bounded drain and migration preservation. Every Critical/Major finding was fixed and re-tested.
The final narrow re-review reports no remaining Critical or Major finding.

## Not delivered / future ownership

- P10 import/identity matching, any probabilistic merge/review ledger work and external provider
  adapters were not started.
- P11 recommendation generation/evaluation and P12 GPU enrichment were not started. P09 delivers
  only the canonical interaction capture/projection boundary they consume.
- P13 Wave and P14 production TLS, backup/restore, security soak, physical Samsung A55 and release
  evidence remain future-phase work.

## Risks and debt

- No known P09 product blocker remains. Production-scale soak, real backup/restore and physical
  device qualification remain explicitly P14-owned.
- The disposable AVD was stopped and its registered definition removed. The execution safety layer
  refused recursive deletion of the now-unregistered residual local AVD data directory; it is not a
  repository artifact, active device or product dependency.

## Repository and cleanup state

- Branch: `codex/autplay-harness-v1`.
- HEAD observed at handoff: `0023fa9ad9d12633ad988230662fbd69bb74eb20`.
- P04-P09 and harness work remains intentionally uncommitted in the shared dirty worktree; unrelated
  user changes were preserved and no commit, push, PR, publish or deployment was performed.
- No P09 Docker container/project remained after verification. The API 26 emulator process is
  stopped and the AVD is no longer registered.

## Exact next prerequisite

P10 is eligible but not started. Continue only with
`docs/build-pack/prompts/P10_import_identity.md`, after reading this handoff, the accepted ADR-019
exact-byte clarification and the P10 design inputs. Do not treat P09 recommendation interaction
facts as P11 recommendation generation.

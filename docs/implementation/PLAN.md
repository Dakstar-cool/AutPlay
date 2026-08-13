# AutPlay Implementation Plan

**Baseline:** P01 monorepo foundation

**Planning horizon:** P01-P14

**Execution rule:** one explicit phase at a time; no future-phase implementation

## Repository state after P01

- The 40 original build-pack files remain at stable root and `docs/` paths; P00 governance remains authoritative.
- P01 added a minimal typed Python package, standalone Compose shell, placeholders, exact lock/wrapper/catalog pins, one disposable PostgreSQL+pgvector service, canonical scripts and platform-neutral CI plan.
- The server has no endpoint or product/domain behavior; Android has no Room/Media3/network/DI behavior; no executable persistence, contract or feature work exists.
- P02 owns executable PostgreSQL persistence. P00-D003 is accepted and applied to the normative reference contract; P02 remains `NOT_STARTED` until a separate explicit phase request.
- The current dependency graph is sequential P00-P11, with P12 and P13 optional only through explicit approved deferral before P14.

## Phase deliverables and gates

| Phase | Dependencies | Repository deliverables | Required exit evidence | MVP acceptance |
| --- | --- | --- | --- | --- |
| [P01](../build-pack/prompts/P01_monorepo_foundation.md) | P00 handoff | Minimal `apps/android`, `server`, `contracts`, `deploy/compose`, test, ADR, wrapper/lock, README, and CI-smoke skeleton; exact validated toolchain pins | Frozen Python checks, Android build/unit smoke where environment supports it, Compose config/health, CPU dependency audit | A-001 |
| [P02](../build-pack/prompts/P02_postgresql_persistence.md) | Green P01; accepted P00-D003 applied | Alembic revisions, typed SQLAlchemy mappings, PostgreSQL fixtures and object/invariant tests matching reference DDL | Real PostgreSQL 18 upgrade/downgrade/upgrade; 57 tables, 53 explicit indexes, 13 functions, 40 triggers, named constraints and drift evidence | A-003, A-004 |
| [P03](../build-pack/prompts/P03_server_runtime.md) | Green P02 | Typed config, FastAPI/worker entrypoints, health, redacted structured logs, owner/device sessions, PostgreSQL lease worker | CPU-only API/worker start; auth, config, health, lease/retry/cancel and CUDA-import tests | A-002 |
| [P04](../build-pack/prompts/P04_sync_contract.md) | Green P03; aggregate-ID mapping P00-D006 resolved | Sync Protocol v1, event JSON Schemas, OpenAPI operations, valid/invalid golden vectors and compatibility policy | Schema/OpenAPI validation and language-neutral expected outcomes for all vectors | Contract prerequisite for A-018-A-022 |
| [P05](../build-pack/prompts/P05_android_foundation.md) | Green P04; Android compatibility decision recorded | Offline-launching Compose shell, Room v1 entities/DAOs/exported schema, FTS, transaction runner, Journal/cursor/tombstone/conflict storage | Room 3 compatibility gate; fresh/open/restart, R8/FTS, transaction failure, unknown-value and lease tests | A-005-A-007 |
| [P06](../build-pack/prompts/P06_vault_streaming.md) | Green P05 | Filesystem Vault adapter, resumable staging, bounded validation/fingerprint, crash-safe commit/reconciliation, authorized HTTP Range stream | Duplicate/concurrent/crash/storage/quarantine tests plus HTTP 200/206/416 and authorization evidence | A-011-A-013 |
| [P07](../build-pack/prompts/P07_library_vertical_slice.md) | Green P06 | Local-first library, playlist, preference/history and search slice across domain/server/client boundaries | Airplane-mode, restart, duplicate/order property, URI loss, FTS, authorization, atomicity and fixture benchmarks | A-008-A-010 |
| [P08](../build-pack/prompts/P08_playback_downloads.md) | Green P07 | Media3 service, source resolver, persistent queue, DownloadService/DownloadIndex reconciliation and player/download UI | Local/Vault playback, process-death restore, network/auth/storage failure, reconciliation and eviction tests | A-014-A-017 |
| [P09](../build-pack/prompts/P09_sync_end_to_end.md) | Green P08 and P04 vectors | Server/Android push-ACK-pull, cursor, tombstone, bootstrap/reset, conflicts and Sync Status | Shared golden vectors plus duplicate/reorder/offline/process-death/bootstrap/two-device convergence evidence | A-018-A-022 |
| [P10](../build-pack/prompts/P10_import_identity.md) | Green P09; T4 terminology P00-D004 resolved | Versioned user-export parsers, adapter ports, resumable import, candidate/evidence matcher, review UI and merge/split audit seam | Golden import/idempotency/resume reports and labeled positive/hard-negative evidence; no unapproved auto-match | A-023-A-025 |
| [P11](../build-pack/prompts/P11_recommendations_cpu.md) | Green P10 | CPU candidates/filters/ranker, explanations, home feed, offline pack and reproducible evaluation harness | Fixed-seed quality/diversity/repeat/latency report, tamper/expiry and cross-user filter tests | A-026-A-028 |
| [P12](../build-pack/prompts/P12_gpu_enrichment.md) | Green P11; explicit execution decision | Isolated optional GPU worker/profile, model registry, versioned embeddings/tags, exact retrieval and benchmark | CPU independence, forced OOM/retry, model parallelism/switch/rollback and RTX 3060 evidence | A-029-A-031 or explicit approved deferral |
| [P13](../build-pack/prompts/P13_wave.md) | Green P08/P09 and explicit execution decision; P11/P12 queues when available | Wave room/queue/clock/preflight/prefetch/reconnect lifecycle | Multi-device authorization, availability, clock/latency/drift/disconnect measurements | A-032, A-033 or explicit approved deferral |
| [P14](../build-pack/prompts/P14_hardening_release.md) | P01-P11 green; P12/P13 green or explicitly approved deferral under P00-D005 | Security/recovery/performance evidence, isolated restore, SBOM/version manifest, runbooks, release artifacts and final matrices | Every applicable A-001-A-040 has evidence and PASS or approved deferral; no critical/high data-loss or object-auth defect | A-034-A-040 and final audit of A-001-A-040 |

## Scope control

- A deliverable is created in its owning phase, not when first mentioned by an earlier design document.
- Interfaces are introduced only for a current real implementation/test seam.
- P00 documentation does not count as P01 bootstrap, P02 schema execution, or product behavior.
- A future acceptance row remains `NOT_STARTED` until its required executable evidence exists.
- P12/P13 are never silently considered complete. Any deferral requires explicit approval and must remain visible in P14 release notes and the acceptance matrix.

## Discrepancies and proposed ADR/change items

Proposed items preserve source documents unchanged. An accepted ADR/change set cites exact sources and updates all affected specifications together.

| ID | Finding / proposed decision | Required before | State |
| --- | --- | --- | --- |
| P00-D001 | Establish execution authority: P00-P14 prompts and latest handoff govern phase order; `AutPlay_Codex_Goal_Schema_Foundation_v1.md` is retained as persistence-design history, not a runnable first phase. | P01 | Operationally accepted in P00/P01 repository contract; source document retained unchanged |
| P00-D002 | Materialize/reconcile Architecture ADR-001-ADR-012 files and statuses. Architecture marks ADR-012 proposed, while ER/physical schemas and later decision baseline already depend on unresolved `UserTrackRef`. | Before broad feature work | Proposed documentation change; no semantic rollback |
| P00-D003 | Add an immutable, general identity decision/evidence/history model and matcher/calibrator/threshold registry. Track Identity sections 12-13 require states, versions, margins, origins, actor and supersession that `importing.import_entry`/`match_candidate` do not fully preserve. | P02 migration implementation | Accepted and applied through ADR-015 and `P00-D003_CHANGESET.md` revision `c108c109d8eb1ab71631ea79e831a20e6cc6811bff5264cb6d1bb38f7433ac71`; P02 remains `NOT_STARTED` |
| P00-D004 | Clarify frozen F-016 terminology: deterministic reuse of one valid known SHA/T4 is not the same as probabilistic identity auto-match, or else keep both disabled until benchmark. Frozen wording cannot be reinterpreted without user approval. | P06 deterministic dedup and P10 matcher | User-approved ADR/change required |
| P00-D005 | Define conditional phase reachability: P14 may follow P11 only when P12/P13 have explicit approved deferrals; otherwise both are prerequisites as shown by the dependency graph. | Before any P12/P13 deferral or P14 | Proposed build-governance ADR |
| P00-D006 | Define local/server aggregate ID mapping in sync envelopes and ACKs. Room preserves local IDs plus nullable server IDs, while the reference server inbox exposes one `aggregate_id`. | P04 contract | Proposed sync-contract decision |
| P00-D007 | Pin/validate Android baseline: Room 3.0.1 preferred, preliminary minSdk 26, JDK/Kotlin/KSP/AGP/Gradle/Compose/Media3/WorkManager compatibility and fallback boundary. | P01/P05 | P01 toolchain accepted in ADR-013; Room/KSP/Media3/WorkManager activation remains a P05 compatibility gate |
| P00-D008 | Pin/validate server baseline: Python, uv, PostgreSQL 18 patch/image digest, pgvector 0.8 patch/digest, Psycopg, SQLAlchemy/Alembic and test tooling. | P01/P02 | P01 exact baseline accepted and smoke-tested in ADR-013; real driver/migration compatibility remains P02 evidence |
| P00-D009 | Define the enforceable CPU-core support matrix. Architecture says Linux/Windows/macOS integration tests; F-005 says cross-platform where practical and Linux x86_64 production. | P01 CI plan/P14 release | Platform-neutral Windows/Linux/macOS CPU job plan recorded; hosted execution and release support policy remain future evidence |

## Recorded document drift

- Architecture section 26 still calls the PostgreSQL major version open; narrower PostgreSQL schema and the decision register fix 18.x. Only the exact patch/image digest remains unresolved.
- Architecture/ER retain several preliminary/open labels already narrowed by PostgreSQL/Room specifications, including database UUID generation and local entity naming.
- `docs/design/AutPlay_PostgreSQL_Schema_v1.md` shows a smoke path under `docs/schema/`; the stable repository path is `docs/design/AutPlay_PostgreSQL_Schema_v1.sql`.
- Sync Protocol v1, OpenAPI skeleton, merge/split contract and physical ADR files are planned future deliverables, not missing P00 package members.
- `START_HERE.md` uses “initial structure skeleton” for P00; P00 interprets this as governance/document structure because P01 explicitly owns code/monorepo skeleton creation.
- Package and component headers use different readiness labels. P00 records the drift but does not rewrite normative sources.

## Intentionally deferred product choices

Do not guess public domain/TLS provider, backup destination/retention budget, first-party external music providers, public registration policy, final recommendation model, Wave public-network policy, publishing/signing accounts, or jurisdiction-specific acquisition policy. Resolve only when an owning phase requires an explicit user decision.

## Stop condition

Stop and ask when a proposed resolution changes a frozen decision, data/security boundary, external provider/legal policy, real data, credentials/paid resources, or requires widening the current phase to pass its gate.

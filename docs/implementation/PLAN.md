# AutPlay Implementation Plan

**Baseline:** P11 CPU recommendations, trusted-local P13 Hybrid Wave and the P14 local RC are complete; ADR-027 explicitly defers only P12 A-030 real-accelerator/model evidence under P00-D005, no GPU model is active, and physical Samsung SM-A556E qualification closes A-038

**Planning horizon:** P01-P14

**Post-RC milestones:** Android frontend M1 and frontend M2 server surfaces are tracked separately
from the closed P00-P14 phase graph. They expose already delivered contracts, add device-local
presentation/state, and must not imply a P15 backend contract or broaden the P14 RC claim.

**Execution rule:** one phase at a time; a trusted manifest edge may continue to its successor only after verified exit gates and standing user authorization

## Repository state during P12 closeout

- The 40 original build-pack files remain at stable root and `docs/` paths; P00 governance remains authoritative.
- P01 added the typed package/Android skeleton, exact lock/wrapper/catalog pins, disposable PostgreSQL+pgvector service, canonical scripts and platform-neutral CI plan.
- P02 added a linear Alembic `0001`-`0010` schema, 57 typed SQLAlchemy row mappings, canonical identity-evidence validation, bounded persistence commands, a loopback-only disposable database fixture, and 225 tests against the pinned real database.
- The executable schema matches 57 tables, 53 explicit indexes, 13 functions and 40 non-internal triggers; metadata/catalog drift is zero and legacy `importing.match_candidate` is absent.
- P03 adds typed settings, operational and device-session FastAPI endpoints, structured redacted logging/API metrics, owner/device session primitives, explicit transaction/clock/ID ports, a PostgreSQL lease worker, and a disposable CPU runtime profile. Both canonical server gates pass 298 tests, and the non-root runtime image/API/worker smoke passes with zero scoped resource residue.
- P03 adds no non-auth product feature endpoint and no schema migration; the Alembic head remains `0010_indexes_privileges`. Password login stays disabled because PostgreSQL schema v1 has no approved credential-persistence contract.
- P04 freezes `AutPlay_Sync_Protocol_v1.md`, the generic sync schemas plus specialized canonical
  listening/impression/feedback schemas, a validated OpenAPI 3.1 contract, and language-neutral
  valid/invalid/hash vectors. The device-independent contract gate is part of both canonical check scripts.
- P04 encodes P00-D006 Variant A/R1, including adopted client IDs, lost-ACK bootstrap reuse, immutable local IDs, canonical-only PostgreSQL IDs, and the accepted P05 durable aggregate-redirect seam.
- User-approved ADR-017 fixes the recommendation evolution boundary: P04 contracts interaction
  capture, P07-P09 implement capture/projection, P11 implements interchangeable CPU pipeline ports,
  and P12 owns isolated embedding generation/model artifacts. Sequential/SONA-Lite remains later.
- P05 has a verified Room 3/KSP/SQLite/R8/API 26 result and a 26-table Room v1 implementation with bundled SQLite/FTS5, standalone outbox, authenticated Journal lineage, bound mutation and explicit atomic materialization, composite binding FKs, sync-state seams, DataStore/Keystore adapters, stable-ID WorkManager scheduling, a playback ownership seam and Compose recreation evidence.
- Accepted ADR-018 resolves fresh standalone intent without fabricating P04 binding/hash members, scopes allocation to a server-compatible device lineage and assigns pure local materialization to P05 while P09 retains consent, authenticated revalidation and transport.
- P06 adds Alembic `0011_vault_runtime`, durable resumable upload receipts, an immutable SHA-256 filesystem CAS, bounded FFmpeg/ffprobe/Chromaprint validation, transaction-separated crash checkpoints, fail-closed exact-byte reuse, reconciliation/quarantine, owner-authorized 200/206/416 streaming, and separate API/CPU-worker/stream processes. A-011-A-013 are verified.
- P07 adds offline library remove/restore, canonical preferences and logical history, duplicate-preserving fractional playlists, SAF/MediaStore intent retention, bounded ranked FTS and actionable Compose flows. Every local aggregate shares one Room transaction with exactly one Journal/outbox fact; attribution remains on its owning event.
- P07 also adds internal owner-scoped PostgreSQL commands and authenticated read-only library/search/playlist/history projections with bounded opaque keyset cursors. It adds no REST write path, sync dispatcher or schema migration. A-008-A-010 are verified.
- ADR-020 activates the documented AGP `9.1.0` compatibility fallback and the RFC 8785 Java canonicalizer for general nested P07 payloads; the full host/device/release/contract gates remain green.
- P08 adds a MediaSessionService/ExoPlayer owner, stable queue-entry media identity, local-first/Vault-at-open resolution, Room v2 queue/listening checkpoints with captured owner and immutable attribution, and one finalized listening event per logical session. Media3 owns DownloadService/DownloadIndex execution and progress; separate stream/download caches enforce protected storage classes. A-014-A-017 are verified.
- ADR-021 resolves Room attribution/session ownership and Media3 cached-representation ownership without fake content URIs.
- P09 implements the frozen P04 protocol end to end: per-event durable server push/ACK, opaque
  pull cursors, authoritative materialized bootstrap, tombstone retention/compaction, canonical
  interaction projection, Android Room v7 profile isolation, bounded WorkManager coordination,
  conflict/dead-letter/status UI and profile-scoped pull/bootstrap projection. A-018-A-022 pass.
- ADR-022 records lost-response/idempotency, transaction, cursor/reset, pre-ACK policy-review,
  tombstone and profile-ownership boundaries.
- P10 adds bounded versioned CSV/JSON/HTML import, durable checkpoint/report/review commands,
  provider-neutral source-adapter ports, immutable shadow candidate evidence, explicit manual
  ImportEntry/UserTrackRef projection, reversible catalog-change plans and an offline Room v8
  review queue. ADR-023 keeps T0-T4 evaluations shadow-only, F-016 activation disabled and live
  external-provider selection deferred. A-023-A-025 pass and form the verified prerequisite for P11.
- P11 adds an interchangeable CPU-only recommendation pipeline with deterministic candidate,
  mandatory-filter, rank/rerank and evaluator ports; immutable pipeline/input evidence; exact and
  algorithmic replay; owner-safe Home/offline-pack APIs; bounded snapshot retention; and complete
  contribution/reason provenance. Room v9 verifies exact RAW_JSON pack bytes, isolates profile/user
  state, applies local-only reranking and records one stable P04 impression at actual presentation.
  ADR-024 keeps the final model deferred. A-026-A-028 pass.
- P12 adds a physically separate `gpu/` uv/image boundary, deterministic NVIDIA inventory and
  auto/UUID/PCI/index selection, verified-artifact/Vault and FFmpeg preprocessing adapters, a
  pinned ONNX CUDA runtime plus actual durable worker composition, fenced
  versioned embedding/tag publication, exact owner-filtered pgvector retrieval and append-only
  benchmark/activation rollback evidence through Alembic `0014`. ADR-025 keeps the model
  experimental; RTX 3060 12 GB is only current measurement hardware and configurable capability
  selection supports upgrades. A-029 and A-031 pass, while A-030 real accelerator OOM and the mandatory
  real RTX tracks/hour/p95/VRAM/quality report remain unavailable on the AMD laptop and prevent a
  green P12 handoff.
- P00-D003 is accepted, applied and executable. User-approved ADR-019 resolves P00-D004 Variant A:
  strict exact-byte technical reuse is separate from probabilistic F-016 matching, does not mutate
  owner projections in P06; P10 records the companion shadow/review lineages without a new server migration.
- P00-D006 Variant A and reviewed P00-D006-R1 are accepted and encoded by P04. Android local IDs remain immutable, the wire carries local plus nullable server IDs, PostgreSQL `aggregate_id` is canonical-only, lost-ACK bootstrap reuses the proven unbound row, and a durable local redirect preserves both local IDs when alias and canonical rows coexist.
- The current dependency graph is sequential P00-P11, with P12 and P13 optional only through explicit approved deferral before P14.
- The Codex Development Harness v1 is a separate repository-tooling milestone. It provides safe local orchestration and a machine-readable companion backlog without advancing P04, changing product APIs, or changing any frozen decision.
- The project-local Stop-hook pipeline consumed its verified P04 -> P05 edge exactly once. No P05 -> P06 edge is currently configured, so completing P05 does not implicitly start P06.

## Phase deliverables and gates

| Phase | Dependencies | Repository deliverables | Required exit evidence | MVP acceptance |
| --- | --- | --- | --- | --- |
| [P01](../build-pack/prompts/P01_monorepo_foundation.md) | P00 handoff | Minimal `apps/android`, `server`, `contracts`, `deploy/compose`, test, ADR, wrapper/lock, README, and CI-smoke skeleton; exact validated toolchain pins | Frozen Python checks, Android build/unit smoke where environment supports it, Compose config/health, CPU dependency audit | A-001 |
| [P02](../build-pack/prompts/P02_postgresql_persistence.md) | Green P01; accepted P00-D003 applied | Alembic revisions, typed SQLAlchemy mappings, PostgreSQL fixtures and object/invariant tests matching reference DDL | Real PostgreSQL 18 upgrade/downgrade/upgrade; 57 tables, 53 explicit indexes, 13 functions, 40 triggers, named constraints and drift evidence | A-003, A-004 |
| [P03](../build-pack/prompts/P03_server_runtime.md) | Green P02 | Typed config, FastAPI/worker entrypoints, health, redacted structured logs, owner/device sessions, PostgreSQL lease worker | CPU-only API/worker start; auth, config, health, lease/retry/cancel and CUDA-import tests | A-002 |
| [P04](../build-pack/prompts/P04_sync_contract.md) | Green P03; aggregate-ID mapping P00-D006 resolved | Sync Protocol v1, generic event/sync plus specialized interaction schemas, OpenAPI operations, valid/invalid/hash golden vectors and compatibility policy | Device-independent contract tests plus both canonical server-only gates; `HANDOFF_P04.md` | Contract prerequisite for A-018-A-022 and future recommendation attribution is PASS; engines remain P09/P11 |
| [P05](../build-pack/prompts/P05_android_foundation.md) | Green P04; Android compatibility decision recorded | Offline-launching Compose shell, Room v1 entities/DAOs/exported schema, FTS, transaction runner, Journal/cursor/tombstone/conflict storage | Room 3 compatibility gate; fresh/open/restart, R8/FTS, transaction failure, unknown-value and lease tests | A-005-A-007 |
| [P06](../build-pack/prompts/P06_vault_streaming.md) | Green P05 | Filesystem Vault adapter, resumable staging, bounded validation/fingerprint, crash-safe commit/reconciliation, authorized HTTP Range stream | Duplicate/concurrent/crash/storage/quarantine tests plus HTTP 200/206/416 and authorization evidence | A-011-A-013 |
| [P07](../build-pack/prompts/P07_library_vertical_slice.md) | Green P06 | Local-first library, playlist, preference/history and search slice plus attributed owning domain/Journal events | Airplane-mode, restart, duplicate/order property, URI loss, attribution/no-duplicate, FTS, authorization, atomicity and fixture benchmarks | A-008-A-010 |
| [P08](../build-pack/prompts/P08_playback_downloads.md) | Green P07 | Media3 service, source resolver, persistent/attributed queue, logical listening finalization, DownloadService/DownloadIndex reconciliation and UI | Local/Vault playback, process-death attribution/hash restore, network/auth/storage failure, reconciliation and eviction tests | A-014-A-017 |
| [P09](../build-pack/prompts/P09_sync_end_to_end.md) | Green P08 and P04 vectors | Server/Android push-ACK-pull, specialized interaction dispatch/projection, cursor, tombstone, bootstrap/reset, conflicts and Sync Status | Shared golden vectors plus exactly-once interaction attribution, duplicate/reorder/offline/process-death/bootstrap/two-device evidence | A-018-A-022 |
| [P10](../build-pack/prompts/P10_import_identity.md) | Green P09; T4 terminology P00-D004 resolved | Versioned user-export parsers, adapter ports, resumable import, candidate/evidence matcher, review UI and merge/split audit seam | Golden import/idempotency/resume reports and labeled positive/hard-negative evidence; no unapproved auto-match | A-023-A-025 |
| [P11](../build-pack/prompts/P11_recommendations_cpu.md) | Green P10 | Model-independent service/ports, composable CPU candidates, filters/rankers, immutable pipeline/replay, attribution, home/offline feed and reproducible evaluator | Generator-swap API stability, fixed pipeline/seed/snapshots, interaction joins, quality/diversity/repeat/latency, tamper/expiry and cross-user tests | A-026-A-028 |
| [P12](../build-pack/prompts/P12_gpu_enrichment.md) | Green P11; explicit execution decision | Isolated optional GPU worker/profile, embedding writer/model registry, versioned embeddings/tags, exact retrieval and benchmark | CPU/API and interaction independence, forced OOM/retry, model parallelism/switch/rollback and RTX 3060 evidence | A-029-A-031 or explicit approved deferral |
| [P13](../build-pack/prompts/P13_wave.md) | Green P08/P09 and explicit execution decision; P11/P12 queues when available | Wave room/queue/clock/preflight/prefetch/reconnect lifecycle | Multi-device authorization, availability, clock/latency/drift/disconnect measurements | A-032, A-033 or explicit approved deferral |
| [P14](../build-pack/prompts/P14_hardening_release.md) | P01-P11 green; P12/P13 green or explicitly approved deferral under P00-D005 | Security/recovery/performance evidence, isolated restore, SBOM/version manifest, runbooks, release artifacts and final matrices | Every applicable A-001-A-040 has evidence and PASS or approved deferral; no critical/high data-loss or object-auth defect | A-034-A-040 and final audit of A-001-A-040 |

## Scope control

- A deliverable is created in its owning phase, not when first mentioned by an earlier design document.
- Interfaces are introduced only for a current real implementation/test seam.
- P00 documentation does not count as P01 bootstrap, P02 schema execution, or product behavior.
- A future acceptance row remains `NOT_STARTED` until its required executable evidence exists.
- P12/P13 are never silently considered complete. Any deferral requires explicit approval and must remain visible in P14 release notes and the acceptance matrix.
- P13 is PASS under ADR-026 with A-032/A-033 evidence. Its verified topology is trusted-local and single API process; public Internet/TLS, cross-instance live fanout and physical WAN qualification remain deferred rather than implicit P13 claims.

## Discrepancies and proposed ADR/change items

Proposed items preserve source documents unchanged. An accepted ADR/change set cites exact sources and updates all affected specifications together.

| ID | Finding / proposed decision | Required before | State |
| --- | --- | --- | --- |
| P00-D001 | Establish execution authority: P00-P14 prompts and latest handoff govern phase order; `AutPlay_Codex_Goal_Schema_Foundation_v1.md` is retained as persistence-design history, not a runnable first phase. | P01 | Operationally accepted in P00/P01 repository contract; source document retained unchanged |
| P00-D002 | Materialize/reconcile Architecture ADR-001-ADR-012 files and statuses. Architecture marks ADR-012 proposed, while ER/physical schemas and later decision baseline already depend on unresolved `UserTrackRef`. | Before broad feature work | Proposed documentation change; no semantic rollback |
| P00-D003 | Add an immutable, general identity decision/evidence/history model and matcher/calibrator/threshold registry. Track Identity sections 12-13 require states, versions, margins, origins, actor and supersession that `importing.import_entry`/`match_candidate` do not fully preserve. | P02 migration implementation | Accepted through ADR-015 and `P00-D003_CHANGESET.md` revision `c108c109d8eb1ab71631ea79e831a20e6cc6811bff5264cb6d1bb38f7433ac71`; applied to normative sources in the preceding commit and implemented/verified by P02 |
| P00-D004 | Clarify frozen F-016 terminology: deterministic reuse of one valid known SHA/T4 is not the same as probabilistic identity auto-match, or else keep both disabled until benchmark. | P06 deterministic dedup and P10 matcher | Accepted by the user on 2026-08-16 as Variant A through ADR-019; P06 owns technical reuse only, P10 owns any future owner-projection representation |
| P00-D005 | Define conditional phase reachability: P14 may follow P11 only when P12/P13 have explicit approved deferrals; otherwise both are prerequisites as shown by the dependency graph. | Before any P12/P13 deferral or P14 | Accepted on 2026-08-17 through ADR-027; P13 PASS and only P12 A-030 real-accelerator/model evidence deferred |
| P00-D006 | Define local/server aggregate ID mapping in sync envelopes and ACKs. Room preserves local IDs plus nullable server IDs, while the reference server inbox exposes one `aggregate_id`. | P04 contract | Accepted on 2026-08-15 through explicit approvals of Variant A and reviewed P00-D006-R1; encoded and validated by P04 without a PostgreSQL migration |
| P00-D007 | Pin/validate Android baseline: Room 3.0.1 preferred, preliminary minSdk 26, JDK/Kotlin/KSP/AGP/Gradle/Compose/Media3/WorkManager compatibility and fallback boundary. | P01/P05/P08 | Room 3.0.1/KSP2/SQLite/WorkManager/DataStore activation, API 26, FTS5 and R8 are verified in P05; Media3 1.10.1 playback/download/service compatibility is verified in P08 |
| P00-D008 | Pin/validate server baseline: Python, uv, PostgreSQL 18 patch/image digest, pgvector 0.8 patch/digest, Psycopg, SQLAlchemy/Alembic and test tooling. | P01/P02 | P01 exact baseline accepted in ADR-013; P02 real driver, migration, mapping, concurrency and cross-shell compatibility evidence is complete |
| P00-D009 | Define the enforceable CPU-core support matrix. Architecture says Linux/Windows/macOS integration tests; F-005 says cross-platform where practical and Linux x86_64 production. | P01 CI plan/P14 release | Platform-neutral Windows/Linux/macOS CPU job plan recorded; hosted execution and release support policy remain future evidence |
| P04-D001 | Define canonical interaction/impression capture before recommendation implementation and keep serving independent from a concrete model. | P04 contract and P07-P12 ownership | User-approved on 2026-08-16 through ADR-017 and `AutPlay_Recommendation_Subsystem_v1.md`; P04 contract evidence delivered, executable work remains with P07-P12 |
| P05-D001 | Preserve fresh standalone mutation without fabricating or later rewriting the authenticated identity/hash of a P04 client event; scope sequence allocation to one device Journal lineage. | P05 Room v1 and P09 materialization/reset behavior | Accepted by the user on 2026-08-16 through ADR-018; implemented and verified by P05 |

## Recorded document drift

- Architecture section 26 still calls the PostgreSQL major version open; the executable baseline resolves it to PostgreSQL 18.4 and the recorded pgvector image digest. The broader normative wording remains document drift.
- Architecture/ER retain several preliminary/open labels already narrowed by PostgreSQL/Room specifications, including database UUID generation and local entity naming.
- `docs/design/AutPlay_PostgreSQL_Schema_v1.md` shows a smoke path under `docs/schema/`; the stable repository path is `docs/design/AutPlay_PostgreSQL_Schema_v1.sql`.
- Sync Protocol v1 and its OpenAPI/event contracts were delivered by P04. Merge/split and physical ADR files remain future owning-phase deliverables, not missing P00 package members.
- `START_HERE.md` uses “initial structure skeleton” for P00; P00 interprets this as governance/document structure because P01 explicitly owns code/monorepo skeleton creation.
- Package and component headers use different readiness labels. P00 records the drift but does not rewrite normative sources.

## Intentionally deferred product choices

Do not guess public domain/TLS provider, backup destination/retention budget, first-party external music providers, public registration policy, final recommendation model, Wave public-network policy, publishing/signing accounts, or jurisdiction-specific acquisition policy. Resolve only when an owning phase requires an explicit user decision.

## Stop condition

Stop and ask when a proposed resolution changes a frozen decision, data/security boundary, external provider/legal policy, real data, credentials/paid resources, or requires widening the current phase to pass its gate.

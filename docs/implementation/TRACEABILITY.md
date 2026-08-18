# AutPlay Traceability

Status meanings:

- `BASELINED`: normative requirement/decision is present and routed, but product behavior is not implemented.
- `IN_PROGRESS`: implementation/evidence exists, but the owning phase gate is not yet complete.
- `PASS`: the stated phase criterion has direct evidence.
- `NOT_STARTED`: the owning future phase has not begun.
- `CHANGE_PROPOSED`: a documented discrepancy needs an ADR/change set before its owning implementation.

## Post-P14 Android frontend M1

| Requirement / decision | Design source | Repository implementation | Tests / evidence | Status |
| --- | --- | --- | --- | --- |
| Android feature navigation and adaptive device classes | Product UI section 94; Android architecture section 7 | Typed destinations; compact bottom navigation and shared rail renderer under `app.autplay.ui`; width classes retained for later content policy | Android lint/debug APK and host unit suite; 89/89 connected tests on physical Samsung SM-A556E, including process restart | PASS for M1 foundation |
| Real Wave frontend without broader media authorization | Wave Protocol v1; ADR-026 | Up to seven invites; create/join/share/leave/close; host queue/pause and clock-calibrated strict start through `WaveCoordinator`; room code remains ephemeral | Existing P13 Wave suites, `FrontendBindingTest`, frontend compile/lint | PASS for contract-backed controls |
| Device-local appearance, scoped music folder and settings transfer | Product settings/import/privacy requirements; Android SAF boundary | Additive non-secret DataStore keys, bounded SAF tree scan into local import review, and bounded secret-free JSON transfer | `SettingsTransferCodecTest`; Android lint/debug APK | PASS for M1 foundation |
| Password/profile/device administration | Product authentication/profile requirements | Contract-backed logout current/all and revoke-current-known-device plus separately named local disconnect; password change and device enumeration remain unavailable | Repository transport tests, physical-device smoke, M2 handoff | PASS for delivered server contracts; broader account management remains NOT_STARTED |

## Post-P14 Android frontend M2 server surfaces

| Requirement / decision | Design source | Repository implementation | Tests / evidence | Status |
| --- | --- | --- | --- | --- |
| Optional API and stream service origins | P06/P08 streaming boundary; local-first architecture | Separate non-secret API/stream origins with legacy one-origin migration, fixed API paths, export exclusion and stream-without-API rejection | Settings/transport host tests; physical API and stream health smoke | PASS |
| Server library and import visibility | Existing P07/P10 server contracts; F-015 unresolved-first rule | Ephemeral bounded library snapshot/search plus durable remote-import job projections and bounded polling; only evidence-safe review actions are exposed | Exact-path/nullable-report tests; import UI safety test; live fixture import, restart and report smoke | PASS |
| Vault upload execution | P06 resumable Vault contracts; WorkManager ownership | Durable intent-only WorkManager input, SHA-256/size preflight, stable idempotency, offset reconciliation, 1 MiB chunks and cancellation; local item must have a server `recording_id` | Host worker/transport tests and physical UI gating | PASS for implementation; real byte E2E awaits an eligible local item |
| Online recommendation serving and replay | P11 immutable recommendation provenance | Direct bounded serve/home/replay calls, persisted response hash/owner/presentation metadata and offline-pack-first Home | Exact route/hash tests; live serve smoke on physical device | PASS |
| Session management | P03 auth contracts and Keystore boundary | Logout current/all, revoke current known device, and explicit local-only disconnect; remote success precedes local credential clearing | Transport ordering/401 tests and physical provisioning smoke | PASS for existing contracts |

## P04 Sync Protocol v1 contract

| Requirement / decision | Design source | Repository implementation | Tests / evidence | Status |
| --- | --- | --- | --- | --- |
| Device/user/profile binding and reset lineage | P04 prompt; P03 auth boundary; Room cursor design | Authenticated `/devices/bind` contract plus required binding fields on push/pull/bootstrap/status; `journal_epoch` future persistence seam | OpenAPI validation, public-schema examples, binding/query parity tests | PASS |
| Event identity, deterministic hash, duplicate and sequence semantics | Offline Journal; PostgreSQL inbox/idempotency tables | Client event schema; RFC 8785/SHA-256 hash rule; bounded 100-event push; APPLIED/DUPLICATE/CONFLICT/REJECTED ACKs | Duplicate same/different payload, reorder, gap, sequence reuse and partial-rejection vectors | PASS |
| Opaque cursor, atomic pull, tombstones, bootstrap and pending intent | Architecture/Room sync transaction rules; PostgreSQL sync/tombstone tables | Pull/bootstrap schemas and OpenAPI; cursor non-offset rule; atomic page/cutover; retained tombstone and preserve/rebase/retry policy | Expired/forged/replay cursor, offline delete, failed page apply and pending-edit bootstrap vectors | PASS |
| Explicit visible conflicts and safe unknown/version behavior | Product conflict minimum; compatibility rules | Conflict taxonomy, REVIEW_REQUIRED boundary, additive-member policy, unsupported push/pull behavior and redacted snapshots/errors | Edit-vs-delete, unknown event/enum, unsupported pulled version, sensitive-key and oversize vectors | PASS |
| P00-D006 Variant A and P00-D006-R1 | Accepted aggregate-ID mapping decision | Dual local/server IDs; canonical-only PostgreSQL ID; adopted create; lost-ACK row reuse; durable P05 redirect-store proposal | Adopt/follow-up/bootstrap/other-device/tombstone/cross-owner/redirect collision-cycle vectors | PASS |
| Canonical listening, actual impression and direct feedback | User-approved ADR-017; Recommendation Subsystem v1 | Generic envelope plus specialized schemas; request/rank/recording/surface attribution; causal impression; no generated-item-as-impression or duplicate domain feedback | Interaction valid/invalid/hash vectors, dispatch/privacy/idempotency and generator-independence contract tests | PASS |
| Future recommendation pipeline evolution | ADR-017/ADR-024/ADR-025; Recommendation Subsystem v1 | Executable P11 candidate/filter/ranker/evaluator ports plus P12 framework-free reader/writer, isolated embedder and versioned rollout boundary; Sequential/SONA-Lite deferred | P11 generator-swap/replay/evaluator/offline evidence; P12 A-029/A-031 and experimental blocker handoff | PASS |
| Language-neutral versioned contract and next-version procedure | P04 deliverables/acceptance | Draft 2020-12 generic/specialized schemas, OpenAPI 3.1 document, valid/invalid/hash/schema-example fixtures, compatibility and versioning policy | `uv run --frozen pytest tests/contract`; both canonical shell gates | PASS |

## P03 server runtime, identity and job foundation

| Requirement / decision | Design source | Repository implementation | Tests / evidence | Status |
| --- | --- | --- | --- | --- |
| Explicit validated configuration precedence and secret isolation | P03 scope/required tests; Decision Register server configuration | `server/src/autplay/runtime/settings.py`; separate API/worker settings; explicit TOML/profile/secret-file/env/override precedence | `server/tests/runtime/test_settings.py`; both 298-test canonical gates | PASS |
| Operational API surface, stable errors and request correlation | P03 scope; Architecture API/observability boundaries | `server/src/autplay/entrypoints/api.py` and `auth_http.py`; operational endpoints plus real refresh/logout/device-revoke routes under `/api/v1` | Runtime/API and real-PostgreSQL auth API tests; runtime Compose liveness/readiness smoke | PASS |
| Redacted structured logs and bounded, low-cardinality metrics | P03 scope; product privacy/security requirements | `server/src/autplay/runtime/logging.py` and `metrics.py` | Logging/API metric-route tests; bearer-value negative assertions; both canonical gates | PASS |
| Local-only first-owner bootstrap and session-bound authentication | P03 scope; no-public-registration constraint | `server/src/autplay/application/auth.py`; PostgreSQL auth repository; composed `autplay-admin bootstrap-owner`; HTTP access/refresh/revoke routes | Unit, HTTP, and real-PostgreSQL auth suites including bootstrap/rotation races | PASS |
| Password path fails closed without credential persistence | P03 password constraint; ADR-016 | Explicit Argon2id primitive is tested, while `password_login_enabled=true` returns a sanitized startup error | `server/tests/test_auth_security.py`; settings negative test; both canonical gates | PASS |
| PostgreSQL job lease, fence, recovery, retry and cancellation | F-008-F-010; P03 job scope | `domain/jobs.py`; job/transaction ports; PostgreSQL repository/UoW; application worker | Eight unit plus thirteen real-PostgreSQL P03 job tests included in both 298-test canonical gates | PASS |
| CPU-only API/worker composition without feature handlers | F-005/F-006; P03 acceptance; A-002 | Separate API and CPU-worker entrypoints; non-root image built from a digest-pinned base; runtime Compose profile; no handler registered by default | Locked process/import tests, prohibited-package audit, UID 999 API/worker runtime smoke, zero scoped residue | PASS |

## P02 PostgreSQL persistence foundation

| Requirement / decision | Design source | Repository implementation | Tests / evidence | Status |
| --- | --- | --- | --- | --- |
| Deterministic executable schema and reversible development lifecycle | P02 scope/acceptance; PostgreSQL Schema v1 | `server/alembic.ini`; migrations `0001`-`0010`; frozen reviewed SQL asset | Clean upgrade → base → upgrade; every adjacent revision pair; one head; `alembic check`; `HANDOFF_P02.md` | PASS |
| Exact reference inventory with no legacy candidate table | Reference SQL; accepted ADR-015 | 57 tables, 53 explicit indexes, 13 `app_private` functions, 40 non-internal triggers; zero activation rows | Migrated/reference catalog snapshots equal; exact names/counts; no `importing.match_candidate`; no ANN indexes | PASS |
| Typed persistence mappings without domain behavior | P02 scope; Architecture dependency rules | 57 SQLAlchemy 2 row mappers / 616 columns under `adapters/postgresql`; no relationships or domain/application imports | Mapper configuration, type/default/index/constraint fingerprint, live metadata comparison with zero drift | PASS |
| Immutable identity decision/evidence/policy history | ADR-015; Track Identity; P00-D003 | Six append-only registry/history tables, sealed snapshots, typed queries, projections and bounded persistence commands | Five states, six query types, all decision/evidence fields, 0/1/2/100 candidates, lineage/review/policy/projection negative matrices | PASS |
| Frozen F-016 plus accepted P00-D004 Variant A boundary | F-016; ADR-015; ADR-019 | Initial activation history remains empty; identity T4 evaluation stays SHADOW while P06 uses a separate strict technical exact-byte path | P06 exact-byte single-variant/active-recording/available-replica eligibility, integrity and authorization tests | VERIFIED |
| Canonical/privacy-safe explanation evidence | Accepted P00-D003 JSON rules | RFC 8785 schema-v1 helpers; conservative empty gate metadata; command-level hash/size/privacy validation | N-1/N/N+1 bounds, nested sensitive fields, provider-disabled explanation, raw-payload cleanup, same/different-hash replay | PASS |
| Canonical cross-platform CPU check path | P02 acceptance; F-005/F-006 | Root PowerShell/Bash scripts own dynamic-loopback Compose lifecycle | PowerShell and Git Bash each passed 225 tests on PostgreSQL 18.4/pgvector 0.8.6; CPU dependency audit; zero resources after cleanup | PASS |

## P01 monorepo foundation

| Requirement / decision | Design source | Repository implementation | Tests / evidence | Status |
| --- | --- | --- | --- | --- |
| One-command clean bootstrap and check sequence | P01 acceptance; A-001 | Root `README.md`; `scripts/bootstrap.*`; `scripts/check.*` | Canonical Windows server-only and full checks; clean-index export check recorded in `HANDOFF_P01.md` | PASS |
| Exact frozen CPU server environment | P01 scope/checks; F-005/F-006 | `.python-version`; `server/pyproject.toml`; `server/uv.lock`; typed package boundaries | CPython 3.14.7; frozen sync/lock; import; Ruff; strict mypy; 13 pytest tests; structural JSON dependency audit | PASS |
| Minimal standalone Android Compose shell | P01 scope/acceptance; local-first baseline | Gradle wrapper/catalog; `apps/android`; ADR-013/014 | Exact JDK/SDK/Gradle gates; `lintDebug`, `testDebugUnitTest`, `assembleDebug` | PASS |
| Disposable PostgreSQL 18 + pgvector local service | P01 scope/checks | `deploy/compose/compose.yaml` exact digest; no host port; project-scoped volume | Healthy; runtime PostgreSQL 18.4/vector 0.8.6; post-cleanup 0 containers/volumes/networks | PASS |
| Future contracts, tests and migrations were placeholders at P01 exit | P01 scope/non-goals | P01 commit `48f8198738c6d50988e903cff7a8b4911c4d4615` | Historical P01 file/dependency/scope audit; P02 later populated persistence-owned paths | PASS |
| Hosting-neutral CI matrix when hosting is unknown | P01 scope | `docs/implementation/CI_PLAN.md` | Windows/Linux/macOS CPU jobs plus Linux Android/Compose job and cold-cache policy | PASS |
| Exact toolchain/dependency decisions recorded | P01 acceptance; P00-D007/D008/D009 | ADR-013, ADR-014, `VERSIONS.md` | Official-source compatibility review plus executable gates | PASS |
| P02 was not started and identity-schema prerequisite was resolved at P01 exit | P01 stop rule; accepted P00-D003 | P01 handoff and commit; accepted ADR-015; PLAN/R-014 | Historical P01 evidence; P02 was subsequently executed in its own phase | PASS |

## P00 repository intake

| Requirement / decision | Design source | Repository implementation | Tests / evidence | Status |
| --- | --- | --- | --- | --- |
| All source documents have stable repository paths | P00 Scope 1-3; `START_HERE.md` | Root files, `docs/design/`, `docs/build-pack/` | ZIP/extracted SHA-256 comparison: 40/40 identical; compact inventory | PASS |
| Root instructions preserve normative precedence | `PROMPT_PROTOCOL.md` sections 1-3 | `AGENTS.md` “Source-of-truth precedence” | Manual comparison plus link/fence checks | PASS |
| PLAN maps every P01-P14 phase to outputs and exit acceptance | P00 Acceptance | `docs/implementation/PLAN.md` | Fifteen unique prompts P00-P14; P01-P14 plan table | PASS |
| Risk register includes minimum required failure areas | P00 Acceptance; risk template | `docs/implementation/RISK_REGISTER.md` R-001-R-017 | Data loss, false merge, sync, Vault, URI, provider, GPU, backup, secrets/security and performance rows present | PASS |
| Versions separate design baseline from unresolved pins | P00 Acceptance; `VERSION_POLICY.md` | `docs/implementation/VERSIONS.md` | Baseline, observed environment, P01 resolutions and files-of-record sections | PASS |
| Existing user changes are preserved | P00 Scope/Acceptance | Intake refused overwrite; original worktree contained only unborn `.git` | Initial `git status`; archive extraction overwrite guard; source hash match | PASS |
| Handoff contains exact next phase command/prompt | P00 Acceptance | `docs/implementation/HANDOFF_P00.md` | P01 prerequisite and exact prompt block | PASS |
| All repository-relative Markdown links resolve | P00 Checks | No source rewrite required | 11 relative links checked, 0 broken | PASS |
| Markdown fenced blocks are balanced | P00 Checks | No source rewrite required | 39 Markdown files, 420 fence markers / 210 blocks, 0 unmatched | PASS |
| Phase prompts have no missing or duplicate phase number | P00 Checks | No source rewrite required | Exactly P00-P14; 0 missing, duplicate, out-of-range or H1 mismatch | PASS |
| Compact inventory and Git state are recorded | P00 Checks | P00 handoff | 40 source files before P00 outputs; final `git status` at phase exit | PASS |
| P00 contains no product feature/code/toolchain work | P00 “Не делать” | Only root governance files and `docs/implementation/` outputs added | Extension/path inventory and changed-file review | PASS |

## Normative baseline and discrepancies

| Requirement / decision | Design source | Repository implementation | Tests / evidence | Status |
| --- | --- | --- | --- | --- |
| Frozen decisions F-001-F-024 remain unchanged | `docs/build-pack/DECISION_REGISTER.md` | Routed through `AGENTS.md`, `PLAN.md`, and risk controls | Source files remain byte-identical to build pack | BASELINED |
| Security/privacy/destructive-data constraints outrank other sources | `ТЗ AutPlay.md`; `PROMPT_PROTOCOL.md` | `AGENTS.md` precedence and stop rules | Manual rule comparison | BASELINED |
| Local-first Android and optional server | F-002/F-003; Architecture invariants | P05/P07-P10 local transactions, playback, sync and offline import/review; R-003/R-005 | API 26 and server-independent behavior evidence through `HANDOFF_P10.md` | PASS |
| CPU core independent of GPU | F-005/F-006; Architecture GPU boundary | Separate P12 lock/image/profile; server has only framework-free contracts; R-007 | A-002/A-029 PASS; CPU dependency/import audit and Compose service-set evidence; A-030 real accelerator/model gate is explicitly deferred by ADR-027 | PASS |
| Immutable Vault and distinct identity entities | F-012-F-016; ER/Track Identity/Vault schemas | P02 immutable ledger, P06 Vault/T4 boundary and P10 shadow/review/catalog runtime; R-002/R-004 | A-011/A-024/A-025 PASS; `HANDOFF_P06.md` and `HANDOFF_P10.md` | PASS |
| Offline Journal transaction and durable sync ownership | F-017-F-020; Room/Architecture | P04/P05/P09 sync plus P10 atomic local review/outbox | A-007/A-018-A-022 and P10 offline review atomicity evidence | PASS |
| No destructive migration fallback | F-021; Room/PostgreSQL policies | Alembic chain through `0015`; guarded downgrades refuse provenance/derived-data loss; named Room migrations through v10 | A-003/A-006, API 26 v1-v10 preservation and A-034 independent PostgreSQL/Vault restore evidence | PASS |
| Direct/local playback before Vault/transcode fallback | F-022; Architecture streaming | P06 authorized Range stream; P08 readable-content/Media3-download/fresh-Vault resolver and auth-at-open data source | P06 stream suite; P08 resolver/Media3/API 26 process tests; A-013-A-017 | PASS |
| Authorized adapters only; no DRM/secret scraping | F-023/F-024; product security | P10 capability manifests, user-owned generic/local adapters and credential-free provenance; R-006/R-009 | Parser/manifest/bounds fixtures and API negatives in `HANDOFF_P10.md`; P14 audit remains | PASS |
| Identity decision history/state must retain all narrower-spec evidence | Track Identity sections 12-13; PostgreSQL acceptance | Accepted ADR-015/P00-D003; executable migrations, typed mappings and persistence commands | Full-field round-trip, sealed evidence, append-only lineage, policy/projection/concurrency suites in `HANDOFF_P02.md` | PASS |
| Benchmark-gated auto-match vs deterministic T4 reuse terminology | F-016; Track Identity sections 7.1/11/12; ADR-019/ADR-023 | Strict technical reuse outside projection; P10 persists T0-T4 shadow evidence and requires explicit reviewed owner projection | P06 technical-reuse tests; P10 all-shadow/no-projection and benchmark-hash evidence | PASS |
| Current phase authority vs legacy schema goal | Current P00-P14 build pack; legacy Codex goal | P00-D001 and `AGENTS.md` execution rule | Package/phase audit | CHANGE_PROPOSED |
| P12/P13 prerequisite or explicit deferral before P14 | `PHASE_INDEX.md` dependency graph and deferral note | P00-D005; ADR-027; `PLAN.md`; R-015 | P13 PASS; only P12 A-030 real-accelerator/model evidence is visibly `DEFERRED_WITH_APPROVAL`; CPU baseline remains authoritative | PASS |
| Sync envelope must preserve local/server aggregate identity | F-017; Room local/server ID pattern; PostgreSQL sync inbox | Accepted P00-D006 mapping encoded in Sync Protocol v1: immutable local ID, nullable server ID in push, canonical-only PostgreSQL aggregate ID, lost-ACK reconciliation and durable local redirect | P04 schemas/OpenAPI plus complete ID/adoption/redirect golden-vector matrix | PASS |
| Recommendation evolution must not lock API/data capture to one model | Product recommendation/privacy requirements; ADR-017/ADR-024 | Model-independent P11 DTOs/ports, P04/P09 interaction contracts and deferred P12 embedding boundary | Generator swap preserves DTOs; CPU graph has no ML/GPU imports; P04/P09 remains sole feedback path | PASS |

## MVP acceptance ownership

The authoritative requirements and statuses remain in [`MVP_ACCEPTANCE_MATRIX.md`](../build-pack/MVP_ACCEPTANCE_MATRIX.md). Each completed phase adds direct evidence without claiming future product behavior.

| Requirement | Design source | Planned implementation | Required tests / evidence | Status |
| --- | --- | --- | --- | --- |
| A-001 Clean repository bootstrap | MVP matrix; P01 prompt | P01 monorepo/toolchain skeleton | Root scripts, platform-neutral CI plan and clean-index export evidence in `HANDOFF_P01.md` | PASS |
| A-002 CPU-only server starts without CUDA | MVP matrix; F-005/F-006 | P03 API/worker runtime and runtime Compose profile | `HANDOFF_P03.md`; locked API/worker process tests, dependency/import audit, non-root Compose smoke | PASS |
| A-003 PostgreSQL clean upgrade/downgrade/upgrade | MVP matrix; PostgreSQL schema | P02 Alembic chain | Real pinned PostgreSQL 18 lifecycle and equal first/second head snapshots in `HANDOFF_P02.md` | PASS |
| A-004 DB invariants match reference DDL | MVP matrix; reference SQL | P02 mappings/migrations | 57/53/13/40 catalog equality, zero metadata drift and 225-test evidence in `HANDOFF_P02.md` | PASS |
| A-005 Android DB fresh create/open/restart | MVP matrix; Room schema | P05 Room v1 with `BundledSQLiteDriver`, offline Compose shell and accepted ADR-018 | API 26 fresh/open/restart, configured and no-profile Activity recreation, standalone persistence and final `HANDOFF_P05.md` | PASS |
| A-006 No destructive Room fallback | MVP matrix; F-021 | Explicit Room schema v1/v2, named additive `MIGRATION_1_2`, WAL, bundled SQLite and no destructive fallback | Exact 26-table v1/v2 schema hashes, API 26 migration/FK negatives, source audit and minified release/R8 gate | PASS |
| A-007 Local mutation + Journal atomic | MVP matrix; F-018; ADR-018 | P05 owns atomic standalone domain+outbox, bound domain+lineage+Journal and explicit idempotent materialization transactions | API 26 commit/restart/rollback/retry/profile-lineage/composite-FK cases and P04 RFC 8785 golden hash pass | PASS |
| A-008 Offline library works without server | MVP matrix; local-first requirements | P07 Compose flows and `LibraryVerticalSliceRepository`; no network dependency | API 26 add/remove/restore/restart, all-aggregate rollback, SAF missing/revoked retention and `HANDOFF_P07.md` | PASS |
| A-009 Duplicate Track entries preserved in playlist | MVP matrix; ER/Room playlist rules | Stable playlist-entry IDs, duplicate refs, fractional base-62 positions and bounded rebalance | Randomized host property tests; API 26 duplicate/reorder/restart and 1,000-entry p95/p99 baseline; `HANDOFF_P07.md` | PASS |
| A-010 Local FTS rebuilds and handles Cyrillic/Latin | MVP matrix; Room FTS | External-content FTS5, safe bounded query builder, deterministic BM25/row-ID ranking and import projection | API 26 rebuild/import/Cyrillic/Latin/transliteration/punctuation/hostile-limit cases; 10,000-row top-50 p95 `11.3139 ms`; `HANDOFF_P07.md` | PASS |
| A-011 Vault commit immutable by SHA-256 | MVP matrix; F-012 | P06 no-overwrite same-filesystem hard-link CAS with fsync, commit/reconcile SHA verification, O(1) commit-time size/mtime stream proof and recoverable quarantine | Filesystem duplicate/conflict/same-size-corruption tests; concurrent real-PostgreSQL same-SHA convergence; pinned-image hostile-media smoke; `HANDOFF_P06.md` | PASS |
| A-012 Ingest resumes safely after process failure | MVP matrix; ingest architecture | P06 durable upload/chunk state, transaction-separated prepare/publish/finalize checkpoints and bounded reconciliation | Real PostgreSQL publication-window retry, missing staging, expired session, corrupt/orphan object and terminal-job reconciliation tests; `HANDOFF_P06.md` | PASS |
| A-013 HTTP Range streaming correct | MVP matrix; stream contract | P06 isolated owner-authorized direct stream | HTTP 200/206/416, open/suffix/If-Range/HEAD, disconnect close, same-size integrity refusal and cross-owner masking tests; `HANDOFF_P06.md` | PASS |
| A-014 Local playback preferred | MVP matrix; F-022 | Local readability probe before any Vault resolution; queue-entry MediaItem mapping | Readable test provider, zero-fallback pure policy and real ExoPlayer READY on API 26; `HANDOFF_P08.md` | PASS |
| A-015 Vault fallback when local URI missing | MVP matrix; Room URI policy | Retained repairable local state plus fresh stable Vault reference authorized at open | Permission-denied provider retains UserTrackRef, resolver fallback, 401 single refresh and Vault ExoPlayer READY; `HANDOFF_P08.md` | PASS |
| A-016 Media3 owns durable download progress | MVP matrix; F-019 | Singleton DownloadService/Manager/Index; Room intent/coarse state only; separate caches and admission/eviction | Interrupted range resume, service recreation/background completion reconciliation, duplicate callback and storage policy tests; `HANDOFF_P08.md` | PASS |
| A-017 Queue restores after process death | MVP matrix; Room queue | Room v2 snapshot/entries, 15-second checkpoint, deterministic shuffle seed, repeat and captured logical session | v1→v2 migration, reopen and two-stage adb force-stop/service restore on API 26; `HANDOFF_P08.md` | PASS |
| A-018 Sync push idempotent | MVP matrix; sync requirements | P09 per-device serialized inbox/idempotency and immutable Android retry | P04 duplicate/hash vectors; real-PG exact replay, changed-hash, lost-response and semantic uniqueness; API 26 duplicate ACK paths; `HANDOFF_P09.md` | PASS |
| A-019 Cursor never advances on partial apply | MVP matrix; sync transaction rule | One preflighted Room transaction per page; opaque cursor acknowledged only when presented | API 26 malformed/unknown/reordered/incomplete page rollback and bounded drain; real-PG cursor binding/reset; `HANDOFF_P09.md` | PASS |
| A-020 Tombstones retained through ACK window | MVP matrix; F-021 sync retention | Transactional soft delete + sync event + retained tombstone; all-active-device compaction gate | Real-PG lagging-device compaction and bootstrap tombstones; API 26 parentless delete retention; `HANDOFF_P09.md` | PASS |
| A-021 Dirty local edit not overwritten by pull | MVP matrix; Room sync rule | Clean-only projection with deterministic profile-scoped conflict evidence | API 26 dirty-delete/no-overwrite, duplicate-conflict and two-profile same-server-ID isolation; `HANDOFF_P09.md` | PASS |
| A-022 Bootstrap/reset preserves pending local intent | MVP matrix; sync bootstrap | Durable materialized owner snapshot and independent Room bootstrap state | Real-PG fixed snapshot/token binding; API 26 invalid cursor, multi-page cutover, process-safe pending Journal and v2-v7 migration; `HANDOFF_P09.md` | PASS |
| A-023 User export import is resumable/auditable | MVP matrix; migration requirements | Bounded versioned adapters plus durable job/checkpoint/report and idempotent row keys | CSV/JSON/HTML golden fixtures; pause/cancel/resume/replay real-PG tests; Room restart/URI evidence; `HANDOFF_P10.md` | PASS |
| A-024 Ambiguous match never silently auto-merges | MVP matrix; F-015/F-016 | T0-T4 shadow-only evaluation; explicit typed manual review; matcher can only propose catalog changes | Hard-negative/tie/conflict benchmark; real-PG no-projection assertions; concurrent catalog tests; Android shadow/review tests; `HANDOFF_P10.md` | PASS |
| A-025 Fingerprint version/provenance persisted | MVP matrix; Track Identity | Versioned fingerprint/source evidence and immutable decision/candidate history | Exact identifier/fingerprint union, incompatible-version, duplicate-row lineage and reprocessing tests; `HANDOFF_P10.md` | PASS |
| A-026 Recommendation baseline reproducible | MVP matrix; recommendation requirements | Deterministic CPU sources/ranker/evaluator with immutable pipeline/input snapshots | Fixed seed/snapshot, exact+algorithmic replay, generator swap, candidate/quality/diversity/latency report; `HANDOFF_P11.md` | PASS |
| A-027 Availability/ACL filters before serving | MVP matrix; authorization requirements | Mandatory owner/ACL/availability/identity/dislike/exclusion filter before scoring | Fail-closed state matrix, authorized non-library cold start, cross-owner API/FK and real-PostgreSQL evidence; `HANDOFF_P11.md` | PASS |
| A-028 Offline pack verified by hash/version | MVP matrix; offline pack schemas | Canonical RAW_JSON producer plus Room v9 owner/profile verifier and presentation mapping | Exact-byte hash, version/encoding/expiry/tamper/owner rejection, source-rank preservation and API 26 evidence; `HANDOFF_P11.md` | PASS |
| A-029 GPU worker isolated | MVP matrix; F-006 | Separate `gpu/` uv lock/image and opt-in Compose service; framework-free server ports | CPU graph audit; runtime-only service set excludes `ml-gpu`; GPU profile has no ports/API dependency; `HANDOFF_P12.md` | PASS |
| A-030 GPU OOM degrades without core outage | MVP matrix; GPU policy | Bounded batch halving, checkpoint resume, generic bounded retry and bounded optional process restart | ADR-027 retains forced-handler/restart/CPU-independence evidence and explicitly defers real CUDA OOM/RTX/model metrics; no model is active | DEFERRED_WITH_APPROVAL |
| A-031 Model changes create parallel embeddings | MVP matrix; versioned derived data | Immutable reviewed registry, versioned rows/reports and gated activation history | Real-PG A/B coexistence, wrong dimension/hash rejection, owner exact retrieval and switch/rollback; `HANDOFF_P12.md` | PASS |
| A-032 Wave preflight catches unavailable media | MVP matrix; Wave requirements | Device/queue-version-bound final-ready reports; normal P06 owner authorization; strict all-present start gate | Real-PG LOCAL/Vault/unavailable and invite/device ACL test; API 26 three-session LOCAL/DOWNLOAD/VAULT/unavailable fixture; `HANDOFF_P13.md` | PASS |
| A-033 Wave clock/degraded behavior bounded | MVP matrix; Wave clock | Seven-sample/20-retained estimator, monotonic scheduled Media3 start, hysteretic speed/seek/degraded policy, snapshot-first reconnect | `P13_WAVE_TIMING_2026-08-17.json`; JVM gap/reorder/clock/drift tests; WS catch-up/revoke tests; API 26 deterministic fixture | PASS |
| A-034 Backup restore drill succeeds | MVP matrix; backup architecture | `BACKUP_RESTORE.md` plus executable two-project drill | Production `FilesystemVaultStorage`; healthy APPLY reconciliation; corruption APPLY repairs/quarantines DB object, replica and bytes | PASS |
| A-035 Secrets/private URLs absent from logs/export | MVP matrix; privacy/security | `SECURITY_REVIEW.md`; bounded production-source scanner plus runtime/import redaction | Zero scan findings; 66 targeted tests pass with one documented Windows symlink-privilege skip | PASS |
| A-036 Object authorization prevents cross-user access | MVP matrix; multi-user isolation | Owner-scoped application/repository/API boundaries | Targeted cross-user API/stream/Vault/library/import/recommendation/Wave negatives plus canonical real-PG suite | PASS |
| A-037 Large fixture meets documented p95 targets | MVP matrix; SLOs | `PERFORMANCE_REPORT.md`; deterministic 100k PostgreSQL fixture; `P14_ANDROID_PERFORMANCE.json` | 120 indexed PostgreSQL searches p50/p95/p99 5.525/6.403/6.665 ms; API 26 FTS 9.397/12.555/13.254 ms and Room playlist 8.760/11.876/11.879 ms; all named p95 targets pass | PASS |
| A-038 Android release installs on Samsung A55 | MVP matrix; Room/device gate | Dev-signed v2/v3 RC APK and physical-device-safe install/restart tooling | Physical Samsung SM-A556E install, background transition, battery-policy check, force-stop process death and restart PASS; no uninstall or user-data clearing | PASS |
| A-039 Full offline-to-online end-to-end flow passes | MVP matrix; release scenarios | `P14_ANDROID_SERVER_E2E_2026-08-17.json`, Android acceptance method and `p14_android_server_e2e.py` | One execution crosses atomic file-backed Room journal, process restart, OkHttp/auth/FastAPI/PostgreSQL, post-commit ACK loss, immutable retry and second Android projection; direct DB verification proves exactly one inbox/event/projection | PASS |
| A-040 Release artifacts use pinned versions/digests | MVP matrix; version policy | `P14_RELEASE_INVENTORY.json`; CycloneDX SBOMs and release dependency report | Exact locks/digests/APK/image hashes; root/server/GPU OSV audits report zero vulnerabilities | PASS |

## Traceability rules

- `PASS` requires an evidence path or recorded reproducible command result.
- Baseline design text does not count as implemented behavior.
- Obsolete/stale guidance remains visible through the P00-Dxxx change item that supersedes it operationally.
- Security and data-loss constraints cannot be deferred silently.
- Future phase handoffs update the applicable rows rather than deleting history.

## Codex Development Harness v1 acceptance

| Requirement | Implementation | Evidence | Status |
| --- | --- | --- | --- |
| Frozen installation and CLI entry point | Root `pyproject.toml`/`uv.lock`; `autplay-codex` script | Canonical bootstrap plus `uv run --frozen autplay-codex --help` | PASS |
| Status reports task/route/thread/Git/checks | `cli.py`, `state.py`, `git_safety.py` | CLI smoke and status unit tests | PASS |
| Luna/Terra/Sol routing, milestone persistence, overrides, ambiguity promotion | `routing.py`, `autplay-codex.toml` | Routing unit tests and dry-run smoke | PASS |
| Atomic/corrupt/interrupted/terminal state behavior | `state.py`, `models.py` | Operation lease, revision CAS, replacement-failure, round-trip, resume, terminal and backlog-sync unit tests | PASS |
| Dirty/protected/destructive Git safety | `git_safety.py` | Clean-tree refusal, HEAD compatibility, status/index/byte fingerprint parser tests, and English/Russian command-policy tests | PASS |
| Read-only review and bounded fix/final-test loop | `codex_client.py`, `workflow.py` | SDK boundary, visible/blocking standalone findings, redaction, and workflow scenario tests | PASS |
| Structured result contracts are valid | `schemas/*.schema.json` | Draft 2020-12 schema and sample/rejection tests | PASS |
| Existing product behavior remains unchanged | No product API/migration/Android source changed | Canonical harness plus PowerShell/Git Bash server/database regression gates; Android gate `NOT RUN` because the exact pinned JDK is absent on this host | PASS; Android environment gap recorded in `HANDOFF_HARNESS_V1.md` |
| Product phase order remains authoritative | `AUTPLAY_CODEX_BACKLOG.json`, `backlog.py` | After complete P00-D006 acceptance, `next` returns eligible P04 and cannot select P05+ | PASS |

## Codex verified phase pipeline v1

| Requirement | Implementation | Evidence | Status |
| --- | --- | --- | --- |
| Project-local official Stop hook | `.codex/hooks.json`, `.codex/config.toml` | Parsed hook config; official `decision: "block"` output contract | PASS |
| Completion is not inferred from model prose | `phase_orchestrator.py`, declarative completion evidence and gates | Incomplete simulation supplies a false positive last message and still does not run P05 | PASS |
| P04 gates control P05 eligibility | `AUTPLAY_CODEX_PHASE_PIPELINE.json`, bounded `CheckRunner` | Failed-gate simulation stays queued; successful simulation records completed/started | PASS |
| Continuation is one-shot | ignored pipeline state, exclusive lock, consumed transition ledger, `stop_hook_active` guard | Success then repeated Stop and active-hook tests emit exactly one normal continuation | PASS |
| Missing/corrupt state is safe | strict state loader; explicit one-time initialization | Missing, corrupt, and post-loss tests fail closed without gate execution or P05 continuation | PASS |
| Current package layout remains valid | frozen `autplay-phase-stop validate` command | Manifest/backlog/evidence/hook paths resolve in the current non-ASCII workspace | PASS |

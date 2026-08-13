# AutPlay Traceability

Status meanings:

- `BASELINED`: normative requirement/decision is present and routed, but product behavior is not implemented.
- `PASS`: the stated phase criterion has direct evidence.
- `NOT_STARTED`: the owning future phase has not begun.
- `CHANGE_PROPOSED`: a documented discrepancy needs an ADR/change set before its owning implementation.

## P01 monorepo foundation

| Requirement / decision | Design source | Repository implementation | Tests / evidence | Status |
| --- | --- | --- | --- | --- |
| One-command clean bootstrap and check sequence | P01 acceptance; A-001 | Root `README.md`; `scripts/bootstrap.*`; `scripts/check.*` | Canonical Windows server-only and full checks; clean-index export check recorded in `HANDOFF_P01.md` | PASS |
| Exact frozen CPU server environment | P01 scope/checks; F-005/F-006 | `.python-version`; `server/pyproject.toml`; `server/uv.lock`; typed package boundaries | CPython 3.14.7; frozen sync/lock; import; Ruff; strict mypy; 13 pytest tests; structural JSON dependency audit | PASS |
| Minimal standalone Android Compose shell | P01 scope/acceptance; local-first baseline | Gradle wrapper/catalog; `apps/android`; ADR-013/014 | Exact JDK/SDK/Gradle gates; `lintDebug`, `testDebugUnitTest`, `assembleDebug` | PASS |
| Disposable PostgreSQL 18 + pgvector local service | P01 scope/checks | `deploy/compose/compose.yaml` exact digest; no host port; project-scoped volume | Healthy; runtime PostgreSQL 18.4/vector 0.8.6; post-cleanup 0 containers/volumes/networks | PASS |
| Future contracts, tests and migrations are placeholders only | P01 scope/non-goals | `contracts/*`, `tests/*`, `server/migrations/README.md` | File/dependency/scope audit; no schema, endpoint, contract payload or feature code | PASS |
| Hosting-neutral CI matrix when hosting is unknown | P01 scope | `docs/implementation/CI_PLAN.md` | Windows/Linux/macOS CPU jobs plus Linux Android/Compose job and cold-cache policy | PASS |
| Exact toolchain/dependency decisions recorded | P01 acceptance; P00-D007/D008/D009 | ADR-013, ADR-014, `VERSIONS.md` | Official-source compatibility review plus executable gates | PASS |
| P02 not started and identity-schema prerequisite is resolved | P01 stop rule; accepted P00-D003 | `PROGRESS.md` P02 `NOT_STARTED`; accepted ADR-015; PLAN/R-014 | Normative contract synchronized; no Alembic config/revision, table model or executable schema exists | PASS |

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
| Local-first Android and optional server | F-002/F-003; Architecture invariants | Phase plan P05/P07/P08/P09; R-003/R-005 | Future Android/e2e evidence | BASELINED |
| CPU core independent of GPU | F-005/F-006; Architecture GPU boundary | Phase plan P03/P11/P12; R-007 | Future A-002/A-029/A-030 evidence | BASELINED |
| Immutable Vault and distinct identity entities | F-012-F-016; ER/Track Identity/Vault schemas | Phase plan P02/P06/P10; R-002/R-004 | Future A-011/A-024/A-025 evidence | BASELINED |
| Offline Journal transaction and durable sync ownership | F-017-F-020; Room/Architecture | Phase plan P04/P05/P09; R-003 | Future A-007/A-018-A-022 evidence | BASELINED |
| No destructive migration fallback | F-021; Room/PostgreSQL policies | `AGENTS.md`; R-001 | Future A-003/A-006/A-034 evidence | BASELINED |
| Direct/local playback before Vault/transcode fallback | F-022; Architecture streaming | Phase plan P06/P08 | Future A-013-A-017 evidence | BASELINED |
| Authorized adapters only; no DRM/secret scraping | F-023/F-024; product security | `AGENTS.md`; R-006/R-009 | Future P10/P14 security evidence | BASELINED |
| Identity decision history/state must retain all narrower-spec evidence | Track Identity sections 12-13; PostgreSQL acceptance | Accepted ADR-015 and `P00-D003_CHANGESET.md`; synchronized ER/PostgreSQL reference contract; P00-D003 in `PLAN.md`; R-014 | Normative fields, states, immutable history, version registries and P02 test requirements are aligned; executable persistence remains unimplemented | BASELINED |
| Benchmark-gated auto-match vs deterministic T4 reuse terminology | F-016; Track Identity sections 12.2-12.3 | P00-D004 in `PLAN.md`; R-002 | Exact source conflict recorded; no semantic change made | CHANGE_PROPOSED |
| Current phase authority vs legacy schema goal | Current P00-P14 build pack; legacy Codex goal | P00-D001 and `AGENTS.md` execution rule | Package/phase audit | CHANGE_PROPOSED |
| P12/P13 prerequisite or explicit deferral before P14 | `PHASE_INDEX.md` dependency graph and deferral note | P00-D005; `PLAN.md`; R-015 | Both statements retained and conditional rule made explicit | CHANGE_PROPOSED |
| Sync envelope must preserve local/server aggregate identity | Room local/server ID pattern; PostgreSQL sync inbox | P00-D006; R-013 | Must be resolved in P04 schemas/golden vectors | CHANGE_PROPOSED |

## MVP acceptance ownership

The authoritative requirements and statuses remain in [`MVP_ACCEPTANCE_MATRIX.md`](../build-pack/MVP_ACCEPTANCE_MATRIX.md). P00 assigns ownership and expected evidence without claiming product completion.

| Requirement | Design source | Planned implementation | Required tests / evidence | Status |
| --- | --- | --- | --- | --- |
| A-001 Clean repository bootstrap | MVP matrix; P01 prompt | P01 monorepo/toolchain skeleton | Root scripts, platform-neutral CI plan and clean-index export evidence in `HANDOFF_P01.md` | PASS |
| A-002 CPU-only server starts without CUDA | MVP matrix; F-005/F-006 | P03 API/worker runtime | CPU-only import/start integration test | NOT_STARTED |
| A-003 PostgreSQL clean upgrade/downgrade/upgrade | MVP matrix; PostgreSQL schema | P02 Alembic chain | Real pinned PostgreSQL 18 lifecycle logs/tests | NOT_STARTED |
| A-004 DB invariants match reference DDL | MVP matrix; reference SQL | P02 mappings/migrations | Constraint/object inventory and drift tests | NOT_STARTED |
| A-005 Android DB fresh create/open/restart | MVP matrix; Room schema | P05 Room v1 | Instrumentation/process-restart test | NOT_STARTED |
| A-006 No destructive Room fallback | MVP matrix; F-021 | P05 database configuration | Static/configuration test | NOT_STARTED |
| A-007 Local mutation + Journal atomic | MVP matrix; F-018 | P05/P07 transactions | Failure-injection rollback/commit test | NOT_STARTED |
| A-008 Offline library works without server | MVP matrix; local-first requirements | P07 Android slice | Airplane-mode add/edit/restart scenario | NOT_STARTED |
| A-009 Duplicate Track entries preserved in playlist | MVP matrix; ER/Room playlist rules | P07 playlist use cases | Persistence/order/property test | NOT_STARTED |
| A-010 Local FTS rebuilds and handles Cyrillic/Latin | MVP matrix; Room FTS | P05/P07 search | FTS fixture/rebuild/transliteration tests | NOT_STARTED |
| A-011 Vault commit immutable by SHA-256 | MVP matrix; F-012 | P06 Vault adapter | Duplicate/corruption/failure-injection tests | NOT_STARTED |
| A-012 Ingest resumes safely after process failure | MVP matrix; ingest architecture | P06 staging/jobs/reconciliation | Checkpoint/crash-window tests | NOT_STARTED |
| A-013 HTTP Range streaming correct | MVP matrix; stream contract | P06 authorized stream | 200/206/416, cancellation and authorization tests | NOT_STARTED |
| A-014 Local playback preferred | MVP matrix; F-022 | P08 source resolver | Local-over-server integration test | NOT_STARTED |
| A-015 Vault fallback when local URI missing | MVP matrix; Room URI policy | P08 resolver/reconciliation | Revoked/missing URI scenario | NOT_STARTED |
| A-016 Media3 owns durable download progress | MVP matrix; F-019 | P08 DownloadIndex reconciliation | Callback/process-death ownership test | NOT_STARTED |
| A-017 Queue restores after process death | MVP matrix; Room queue | P08 Media3/Room queue | Instrumentation process-death test | NOT_STARTED |
| A-018 Sync push idempotent | MVP matrix; sync requirements | P04 contract/P09 engine | Duplicate ID/hash golden vectors | NOT_STARTED |
| A-019 Cursor never advances on partial apply | MVP matrix; sync transaction rule | P04 contract/P09 engine | Partial-batch failure test | NOT_STARTED |
| A-020 Tombstones retained through ACK window | MVP matrix; F-021 sync retention | P04/P09 tombstone lifecycle | Delete/offline/resync/compaction test | NOT_STARTED |
| A-021 Dirty local edit not overwritten by pull | MVP matrix; Room sync rule | P04/P09 conflict apply | Dirty-edit conflict vector | NOT_STARTED |
| A-022 Bootstrap/reset preserves pending local intent | MVP matrix; sync bootstrap | P04/P09 reset flow | Invalid cursor/rebootstrap test | NOT_STARTED |
| A-023 User export import is resumable/auditable | MVP matrix; migration requirements | P10 import jobs/parsers | Golden fixture, checkpoint and report | NOT_STARTED |
| A-024 Ambiguous match never silently auto-merges | MVP matrix; F-015/F-016 | P10 matcher/review | Labeled hard-negative dataset/report | NOT_STARTED |
| A-025 Fingerprint version/provenance persisted | MVP matrix; Track Identity | P10 evidence persistence | Persistence/reprocessing/history test | NOT_STARTED |
| A-026 Recommendation baseline reproducible | MVP matrix; recommendation requirements | P11 CPU ranker/eval | Fixed dataset/seed metrics report | NOT_STARTED |
| A-027 Availability/ACL filters before serving | MVP matrix; authorization requirements | P11 filters | Negative authorization/availability tests | NOT_STARTED |
| A-028 Offline pack verified by hash/version | MVP matrix; offline pack schemas | P11 pack producer/client | Tamper/version/expiry tests | NOT_STARTED |
| A-029 GPU worker isolated | MVP matrix; F-006 | P12 process/profile | CPU/API no-CUDA import and Compose profile evidence | NOT_STARTED |
| A-030 GPU OOM degrades without core outage | MVP matrix; GPU policy | P12 bounded worker | Forced OOM/retry-terminal plus core-health test | NOT_STARTED |
| A-031 Model changes create parallel embeddings | MVP matrix; versioned derived data | P12 registry/storage | Model A/B switch/rollback test | NOT_STARTED |
| A-032 Wave preflight catches unavailable media | MVP matrix; Wave requirements | P13 preflight | Multi-device availability fixture | NOT_STARTED |
| A-033 Wave clock/degraded behavior bounded | MVP matrix; Wave clock | P13 timing/reconnect | Timing/disconnect report with declared target | NOT_STARTED |
| A-034 Backup restore drill succeeds | MVP matrix; backup architecture | P14 operations | Isolated restore and checksums | NOT_STARTED |
| A-035 Secrets/private URLs absent from logs/export | MVP matrix; privacy/security | P14 cross-cutting hardening | Redaction, secret-scan and diagnostic tests | NOT_STARTED |
| A-036 Object authorization prevents cross-user access | MVP matrix; multi-user isolation | P14 negative suite | API/stream/job/library cross-user tests | NOT_STARTED |
| A-037 Large fixture meets documented p95 targets | MVP matrix; SLOs | P14 performance suite | Named hardware/dataset p50/p95/p99 report | NOT_STARTED |
| A-038 Android release installs on Samsung A55 | MVP matrix; Room/device gate | P14 release build | Dev/release smoke evidence on named device | NOT_STARTED |
| A-039 Full offline-to-online end-to-end flow passes | MVP matrix; release scenarios | P14 e2e suite | Scenario logs/artifacts | NOT_STARTED |
| A-040 Release artifacts use pinned versions/digests | MVP matrix; version policy | P14 packaging | SBOM/version/digest manifest | NOT_STARTED |

## Traceability rules

- `PASS` requires an evidence path or recorded reproducible command result.
- Baseline design text does not count as implemented behavior.
- Obsolete/stale guidance remains visible through the P00-Dxxx change item that supersedes it operationally.
- Security and data-loss constraints cannot be deferred silently.
- Future phase handoffs update the applicable rows rather than deleting history.

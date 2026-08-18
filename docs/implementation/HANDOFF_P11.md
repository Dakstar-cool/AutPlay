# P11 Handoff - CPU Recommendations, Replay and Offline Packs

## Outcome

P11 is complete and A-026-A-028 are PASS. AutPlay now has a replaceable, explainable and
deterministic CPU-only recommendation baseline with mandatory fail-closed serving filters,
immutable replay/provenance, bounded retained inputs, owner-safe Home/offline-pack APIs and a
reproducible evaluator. Android Room v9 verifies exact offline-pack bytes, reranks locally without
rewriting server rank and records one stable P04 impression only at actual presentation.

The active implementation is a comparison baseline, not the deferred final model. P12 has not
started. Embeddings, CUDA/GPU, Sequential/SONA-Lite and provider selection are not dependencies of
P11 serving.

## Delivered scope

### Server

- Pure model-independent recommendation values and ports for representation, candidate generation,
  composition, mandatory filtering, ranking/reranking, version registry, trace/snapshot storage,
  optional embedding reads and immutable-dataset evaluation.
- Registered CPU sources for explicit preference, organic history/affinity, relevant freshness,
  forgotten content and controlled exploration; cold start works without embeddings or prior
  `UserTrackRef` while authorization and availability still fail closed.
- Canonical Recording deduplication retains every bounded source contribution. Fixed-point scores,
  seeded tie-breaking and deterministic diversity caps make output reproducible.
- Mandatory owner/ACL, online/offline availability, active identity, Dislike and explicit exclusion
  filters execute before ranking. Recommended-only history is not treated as organic affinity.
- Immutable pipeline manifest and retained input snapshot evidence; exact persisted response replay;
  algorithmic replay from original input only; stable `REPLAY_INPUT_UNAVAILABLE` after expiry/purge.
- Owner-scoped bounded purge of expired personal snapshots. Request/items/hashes remain available
  for exact replay while nullable snapshot linkage prevents unbounded retained history documents.
- Model-independent authenticated endpoints for recommendations, Home, offline packs, exact replay
  and algorithmic replay. Unknown pipelines return stable client error `409`.
- RFC 8785 canonical `RAW_JSON` offline-pack v1 with SHA-256 over exact UTF-8 bytes, bounded items
  and payload, exact user/device/request/pipeline/input identity and immutable source rank.
- Reproducible evaluator report with dataset/environment/pipeline identity, candidate Recall@K,
  Precision/Recall/NDCG/MRR/HitRate, coverage/diversity/novelty/repeat and latency statistics.
- P09 hardening: semantic-presentation duplicate lookup applies only to impressions; direct feedback
  proves causal owner/device/request/rank/Recording equality; cross-device impression races return
  the terminal duplicate result rather than 500.

### Android

- Additive Room v8-to-v9 migration, nullable owner only for legacy pack rows and fail-closed legacy
  activation; exported v9 schema hash is recorded below.
- Strict bounded numeric-version `RAW_JSON` v1 verifier for profile/user/device identity, canonical
  bytes, SHA-256, envelope UUIDs, known encoding, timestamps, expiry and item/contribution limits.
- Authenticated bounded OkHttp pack fetch/store path. Cached local Home renders first; server refresh
  is optional and never makes a local user action synchronously depend on the personal server.
- Deterministic local-only Like/Dislike/Skip adjustment and diversity policy preserves immutable
  `recommendation_request_id` and server `source_rank`; unavailable/excluded/disliked items fail
  closed locally.
- Owner/profile-scoped Home sections, artist-affinity recent releases, explicit bounded stale-pack
  fallback and full binding-key UI state isolation.
- Durable presentation mapping keyed by profile, owner, presentation, request and source rank.
  Mapping and the existing P04 Offline Journal impression commit in one Room transaction; retry,
  recomposition and restart reuse the same event ID.

## Decisions

- ADR-024 accepts the deterministic CPU baseline and replay/offline boundary. Activation/routing is
  separate from immutable manifest identity; shadow failure cannot affect served output.
- Snapshot capture and watermark use one `REPEATABLE READ` view. Candidate truncation selects the
  documented deterministic newest 5,000 before stable serialization.
- Expired input snapshots are purged in bounded owner-scoped batches with `FOR UPDATE SKIP LOCKED`;
  snapshot FK uses column-specific `ON DELETE SET NULL`, preserving exact replay evidence.
- Offline transport version is numeric `1`, encoding is `RAW_JSON`, and SHA-256 covers the exact
  canonical payload bytes. Delivery is never an impression.
- P04/P09 remains the only feedback path. No feedback REST endpoint, broker, vector database,
  provider, GPU dependency or future-model decision was added.

## Migrations and contracts

- Alembic head: `0013_recommendation_runtime`, additive from `0012_sync_runtime`.
- Exact PostgreSQL inventory: 64 tables, 60 explicit indexes, 15 helper/constraint functions and
  43 non-internal triggers.
- Reference SQL mirrors are byte-identical, SHA-256
  `ea093ea8f8a2ef2a7d143c4465747fae8e996cd3b8b3ebffde021d2b3fdaf1bf`.
- Room head: v9 with named non-destructive v8-to-v9 migration and normalized schema SHA-256
  `f7764762cdc29efe25c285e53b0cce6c513dfba0e4a491dfc9ffd2bdcb915d62`.
- Public DTOs expose recording/request/rank/reason/contribution/availability concepts only; no
  tensor, embedding, CUDA, internal semantic ID or private stream URL leaks into the contract.

## Principal implementation and evidence paths

- `server/src/autplay/domain/recommendations.py`
- `server/src/autplay/ports/recommendations.py`
- `server/src/autplay/application/recommendations.py`
- `server/src/autplay/adapters/postgresql/recommendations.py`
- `server/src/autplay/entrypoints/recommendation_http.py`
- `server/migrations/versions/0013_recommendation_runtime.py`
- `server/tests/test_recommendations.py`
- `server/tests/runtime/test_recommendation_api.py`
- `server/tests/postgresql/test_recommendation_runtime.py`
- `apps/android/src/main/kotlin/app/autplay/application/recommendation/OfflineRecommendationRepository.kt`
- `apps/android/src/main/kotlin/app/autplay/application/recommendation/OkHttpRecommendationPackTransport.kt`
- `apps/android/src/main/kotlin/app/autplay/data/local/AutPlayDatabase.kt`
- `apps/android/src/main/kotlin/app/autplay/data/local/entity/Entities.kt`
- `apps/android/src/androidTest/kotlin/app/autplay/application/recommendation/OfflineRecommendationRepositoryTest.kt`
- `apps/android/src/androidTest/kotlin/app/autplay/data/local/P11RoomMigrationTest.kt`
- `apps/android/src/androidTest/kotlin/app/autplay/HomeRecommendationScreenTest.kt`
- `docs/adr/ADR-024-p11-cpu-recommendation-replay-and-offline-boundary.md`

## Acceptance evidence

| Acceptance | Result | Evidence |
| --- | --- | --- |
| A-026 reproducible baseline | PASS | Fixed seed/snapshot output and report hashes, generator-swap DTO stability, exact/algorithmic replay, deterministic 5,000 cutoff, quality/diversity/repeat/latency metrics |
| A-027 filters before serving | PASS | Complete disallowed-state matrix, authorized non-library cold start, owner-safe snapshots/requests/packs, cross-owner negative FK/API and offline local filter tests |
| A-028 verified offline pack | PASS | Server canonical exact-byte contract plus Android unknown-version/encoding, hash, tamper, expiry, profile/user/device and restart tests on API 26 |

## Exact verification commands and results

Server scoped and full evidence:

```powershell
uv run --project server --frozen ruff format --check <25 changed Python files>
uv run --project server --frozen ruff check <25 changed Python files>
uv run --project server --frozen mypy server/src server/tests
uv run --project server --frozen pytest -c server/pyproject.toml
```

- Ruff format/check: PASS.
- Strict mypy: PASS across 141 files.
- Full server suite: 405 passed, 1 skipped in 175.13 seconds. The only skip is the existing
  Windows symlink-privilege case.
- Consolidated Alembic lifecycle/reference parity/P09/P11 real-PostgreSQL gate: 31 passed in
  39.62 seconds.
- CPU import graph: no ML/GPU/CUDA imports.

Android evidence with pinned Microsoft OpenJDK 17 and Android SDK:

```powershell
./gradlew.bat --no-daemon --console=plain :apps:android:lintDebug :apps:android:testDebugUnitTest :apps:android:assembleRelease
./gradlew.bat --no-daemon --console=plain :apps:android:connectedDebugAndroidTest
```

- Lint, host tests and minified release/R8: PASS; 45/45 host tests.
- Disposable API 26 x86_64 AVD: 78/78 connected tests passed in 4m14s.
- The disposable AVD and PostgreSQL Compose project/volume/network were removed and verified absent.

Final repository canonical gate:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\check.ps1
```

- Bootstrap: PASS with both frozen locks unchanged.
- Canonical check: PASS after final documentation and import-order correction.
- Root harness: 80 passed; P04 contract: 51 passed; whole server Ruff/format/mypy and CPU audit:
  PASS; Android lint/45 host tests/debug/minified release: PASS; real PostgreSQL server suite:
  405 passed, 1 existing Windows symlink skip in 170.31 seconds; scoped database cleanup: PASS.

## Repeated-error protocol

The final canonical gate reported the same five I001 import-order findings twice because the first
fix ran with automatic per-file config discovery while the canonical script explicitly supplies
`--config server/pyproject.toml` from repository root. Per the project protocol, work stopped and
official Ruff documentation was reviewed. Credible options were: run from the server working
directory; pass the canonical config explicitly; apply `ruff check --select I --fix`; reorder the
blocks manually; or suppress I001. The selected fix reproduced the exact canonical config/context
and ran safe `ruff check --config server/pyproject.toml --fix` on the five files, then verified the
whole server tree. Suppression was rejected because it would hide drift. References:
`https://docs.astral.sh/ruff/configuration/#config-file-discovery`,
`https://docs.astral.sh/ruff/linter/#fixes`,
`https://docs.astral.sh/ruff/formatter/#sorting-imports`, and
`https://docs.astral.sh/ruff/rules/unsorted-imports/`.

Other distinct first-occurrence issues included missing PUBLIC function revokes,
reference-classifier support for additive `ALTER`, version-catalog enforcement for MockWebServer
and one UI vertical-position timeout; each received one targeted correction.

## Independent review

- Server review initially found five Major issues: snapshot visibility, deterministic truncation,
  Home relevance, pack/request owner FK and concurrent impression retry, plus one Minor unknown
  pipeline error. All were corrected and covered by tests.
- Android review initially found three Major issues: missing production pack ingestion,
  same-profile cross-user leakage and stale Home state. All were corrected and re-reviewed.
- Integrated review found one Major retention defect. Bounded physical purge plus nullable linkage,
  exact/algorithmic replay semantics and real-PostgreSQL regression evidence closed it.
- Final re-reviews report zero Critical and zero Major findings.

## Not delivered / future ownership

- No embedding generation, ANN/HNSW, CUDA/GPU runtime, final model, Sequential/SONA-Lite component
  or automatic identity matching; these remain explicitly outside P11.
- No production provider/domain/TLS/backup target, public registration, deployment, publishing or
  signing decision.
- No impression on delivery and no parallel feedback endpoint.
- No P12 implementation has started.

## Risks and debt

- Algorithmic replay is intentionally bounded by the declared input retention window; after purge,
  exact persisted replay remains but algorithmic replay fails closed.
- The 5,000-recording snapshot cutoff is deterministic and documented, but production-scale quality
  and retention sizing remain P14 operational evidence.
- RAW_JSON v1 interoperability is verified on server/JVM/API 26; future encodings require an explicit
  versioned compatibility change.
- API 26 emulator evidence is green; Samsung A55-class physical-device performance remains P14.
- P11 establishes a CPU comparison baseline and does not claim final recommendation quality.

## Repository and cleanup state

- Branch: `codex/autplay-harness-v1`.
- Starting HEAD remains `0023fa9ad9d12633ad988230662fbd69bb74eb20`; P11 is uncommitted because
  the shared worktree already contains preserved P04-P10/user changes.
- No push, PR, deploy, production migration, paid resource or external write was performed.
- Disposable P11 PostgreSQL and API 26 AVD resources were removed; nothing material requires
  recovery.

## Exact next prerequisite

P11 is complete. Stop here. Do not start P12 until the user explicitly chooses to execute P12
instead of an approved deferral and the current green P11 handoff is re-verified. If P12 is chosen,
read `docs/build-pack/prompts/P12_gpu_enrichment.md` and all inputs it names before editing files.

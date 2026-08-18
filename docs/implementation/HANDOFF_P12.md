# P12 Handoff — Isolated GPU Enrichment and Embeddings

**Outcome:** `BLOCKED` (implementation is experimental; A-029 and A-031 PASS, A-030 IN_PROGRESS)

**Date:** 2026-08-17

## Summary

P12 delivered an isolated optional GPU project and image, deterministic NVIDIA inventory with
automatic or explicit future-proof device selection, a pinned ONNX CUDA adapter and actual durable
worker composition, immutable reviewed-model/benchmark provenance, verified local Vault/model
resolution, bounded deterministic audio preprocessing, fenced/versioned embedding and tag writes,
exact owner-safe pgvector retrieval, and guarded A/B activation/rollback/retirement state.

The development laptop has only AMD integrated graphics and no `nvidia-smi`. The current server is
an NVIDIA RTX 3060 12 GB (GA106, compute capability 8.6), but it is measurement hardware rather
than an application requirement and is not accessible from this workspace. No reviewed model
artifact has passed its gate. The Compose GPU service therefore fails before claiming work and has
only bounded process restart. Real CUDA OOM plus tracks/hour, p95 job time, peak VRAM and quality
delta cannot be produced here. No metric was fabricated, no model is ACTIVE, and P13 has not started.

## Delivered scope

- Separate `gpu/` uv project, lock and digest-pinned non-root/read-only-capable image; CPU canonical
  bootstrap/check never syncs, builds, pulls or starts it.
- `AcceleratorInventory` contract and bounded `nvidia-smi` implementation. `auto` selects the
  highest compatible compute capability, total/free VRAM and UUID; manual selectors are
  `uuid:`, `pci:` and `index:`. Display-name selection is deliberately rejected. Minimum compute
  capability/VRAM are configuration, so a compatible upgraded card needs no business-logic edit.
- Opt-in Compose `gpu` profile with no published port or API/CPU dependency, Vault read-only and a
  private model cache. Runtime-only service set remains `postgres/migrate/api/stream/worker-cpu`.
- Approved registry with RFC 8785 artifact/preprocessing manifests, SHA-256 weights/provenance,
  license review reference, runtime/dimension/status and DB-level prevention of direct ACTIVE
  insertion or activation without an APPROVED report.
- Generic job payload contains only `enrichment_job_id`; typed DB target resolves Recording,
  AudioVariant, model and expected hashes. Arbitrary job URL/path is rejected.
- Bounded FFmpeg mono-float32 segment preprocessing and deterministic input/vector hashes.
- GPU-owned verified artifact loader and deterministic batch/pool/normalize implementation behind
  an allowlisted ONNX Runtime CUDA 1.26.0 adapter. It verifies a symlink-free hash-addressed path,
  size/SHA-256, model runtime/format/precision and selected CUDA device before any job claim.
- Runnable `autplay-ml-gpu` composition wires registry/target access, verified read-only Vault CAS,
  FFmpeg preprocessing, artifact-backed embedder, handler registry and the existing PostgreSQL
  lease worker. Compose retries a failed process at most three times.
- The runnable contract is deliberately only `ml.audio-embedding/v1`: target kind, durable key,
  schema version and model task are bound in domain/handler/writer/DB checks. A standalone
  `AUDIO_TAGS` target is rejected until a real handler exists; optional tags returned by an
  embedding model still publish atomically to versioned `recording_tag_set` rows.
- Bounded OOM batch halving, durable checkpoints, resumed reduction state, generic bounded retry,
  lease fencing and idempotent/conflict-aware embedding/tag publication.
- Real-PostgreSQL process-lifecycle evidence expires a simulated dead process lease, recovers its
  `OOM_REDUCED` checkpoint in a new worker and publishes exactly one derived row at attempt two.
- Alembic `0014_gpu_enrichment`: model provenance additions plus immutable benchmark reports,
  activation history, typed enrichment targets and tag sets. Downgrade refuses when any P12 data
  would be destroyed.
- Parallel derived rows per model/source, wrong dimension/model hash rejection, strict tag
  conflicts, activation only from APPROVED evidence, immediate-predecessor rollback only inside
  the recorded window, and bounded rollback-protected retirement.
- Exact cosine retrieval over the requesting user's authorized, committed, valid and available
  Vault set, with dislike/exclusion filtering and stable Recording-ID ties. No HNSW index was added
  because no named target-scale exact-latency failure exists.
- One candidate family only, LAION-CLAP music, recorded as research rather than downloaded or
  approved. Checkpoint license/provenance/hash/runtime remain part of the server-side gate.

## Not delivered / blockers

- No reviewed checkpoint is installed or registered. The concrete ONNX CUDA process exists, but
  fails closed before a durable claim when `AUTPLAY_GPU_MODEL_ID`, an eligible registry row or its
  hash-addressed private artifact is absent.
- No RTX-class device is accessible from this workspace. Real accelerator OOM, tracks/hour, p95
  job time, peak VRAM and quality delta remain unavailable. The database restart/lease-recovery
  path is covered without pretending that it is an accelerator benchmark.
- No model activation and no P11 serving replacement occurred. The P11 CPU baseline remains the
  only serving path.
- No standalone audio-tag model/handler is claimed. The schema rejects `AUDIO_TAGS` jobs instead
  of allowing durable work that no process can claim.
- HNSW remains absent by decision, not deferred implementation debt.

## Decisions

- [ADR-025](../adr/ADR-025-p12-isolated-gpu-enrichment-and-model-rollout.md) is accepted under the
  standing technical-decision authorization.
- Hardware identity is vendor-neutral at the server port; P12 implements NVIDIA discovery only.
  UUID/PCI are stable manual selectors and `auto` handles a later upgraded/multi-GPU server. RTX
  3060 / GA106 / sm_86 / 12 GB is the current benchmark record, not a hard requirement.
- Exact pgvector remains the reference retrieval and re-score path until measured data justifies
  approximate indexing.
- Missing hardware/artifact is an evidence blocker, never permission to add CUDA to `server/` or
  invent benchmark results.

## Persistence and contracts

- Alembic head: `0014_gpu_enrichment` over `0013_recommendation_runtime`.
- Reference inventory: 68 tables, 64 explicit indexes, 19 functions, 49 non-internal triggers and
  777 mapped columns.
- Synchronized reference SQL SHA-256:
  `168a829d9d2c53e1f14d94f74514f198f89d07c761010b3eb996dd46038d21ab`.
- GPU lock SHA-256:
  `3cc1623154517ed4c4e5f374901420536eacb89e5ce4d394da41b453ee1b8198`.
- P11 public recommendation and P04 interaction schemas are unchanged. P11 depends only on the
  framework-free `TrackEmbeddingReader` port.

## Principal changed paths

- `gpu/`: separate project, Dockerfile, inventory/settings/entrypoint, PostgreSQL/Vault/model
  resolution, preprocessing, verified-artifact embedding, ONNX CUDA runtime, worker composition
  and 21 passing tests (plus two Windows symlink skips).
- `deploy/compose/compose.runtime.yaml`, `.dockerignore`, `scripts/test-p12-gpu.ps1`.
- `server/migrations/versions/0014_gpu_enrichment.py`, both synchronized reference SQL mirrors,
  migration/schema counts and Alembic head.
- `server/src/autplay/domain/enrichment.py`, `application/enrichment.py`, `ports/enrichment.py`,
  `ports/recommendations.py`.
- `server/src/autplay/adapters/postgresql/enrichment.py`, ML mappings/metadata/readiness.
- P12 unit, runtime, mapping, migration and real-PostgreSQL tests.
- ADR-025, evidence probe, README/Compose/CI/plan/progress/risk/version/traceability/matrix docs.

## Commands and observed results

- P11 prerequisite scoped unit/API suite: 13 passed before P12 edits.
- `uv lock --project gpu --check`: PASS; 56 resolved packages in the isolated ONNX/CUDA lock.
- GPU Ruff/format/strict-mypy: PASS.
- `uv run --project gpu --frozen pytest gpu/tests`: 21 passed, 2 skipped because Windows denied
  symbolic-link creation; the production paths reject symbolic links component by component.
- Selected server Ruff/format/strict-mypy and CPU import/API tests: PASS; 23 selected tests passed.
- Direct online Docker builds repeatedly encountered different dependency-host DNS failures inside
  BuildKit. Per the repeated-error protocol, official Docker/uv alternatives were reviewed and an
  exact lock-exported Linux wheelhouse was used for an offline-equivalent validation build. PASS:
  ONNX imports and lists `CUDAExecutionProvider`; non-root/read-only `--check-config` passes; local
  After review fixes, current server/GPU wheels were overlaid offline on the same validated locked
  dependency image and the provider/import assertions were repeated. Final local image
  manifest-list SHA-256
  `e3020d6b29c2695deb8ae3eb0decc6d5b78fd668afc69bde8bc520926411fa33`, size 2,297,185,893 bytes.
  The temporary 2.09 GB wheelhouse and validation Dockerfile were removed after the successful build.
  Reviewed sources: Docker Desktop [networking/DNS](https://docs.docker.com/desktop/features/networking/networking-how-tos/),
  BuildKit [configuration](https://docs.docker.com/build/buildkit/configure/), Dockerfile
  [network/cache mounts](https://docs.docker.com/reference/dockerfile/), and uv
  [HTTP/offline settings](https://docs.astral.sh/uv/configuration/environment/).
- Compose profiles: `runtime` excludes `ml-gpu`; `runtime+gpu` includes it; GPU has no ports and API
  has no GPU dependency.
- `uv run --project gpu --frozen autplay-ml-gpu --list-devices`: expected exit 3 with
  `gpu_accelerator_unavailable`; retained in
  `evidence/P12_GPU_HARDWARE_PROBE_2026-08-17.json`.
- First canonical `scripts/check.ps1 -ServerOnly`: 405 passed, 1 skipped, 4 failed; exposed stale
  P12 test counts/fields and ORM constraint-name drift, all corrected.
- Second canonical attempt stopped at format drift before DB; formatting corrected.
- Third canonical DB run: 408 passed, 1 skipped, 2 failed; exposed non-atomic ACTIVE switch order
  and mapping fingerprint drift, both corrected.
- Final `scripts/check.ps1 -ServerOnly`: PASS — 80 harness tests, 51 contract tests and 416 server
  tests passed; one expected Windows symlink-privilege test skipped. PostgreSQL 18.4/pgvector 0.8.6,
  Alembic zero drift, adjacent/data-guarded downgrade coverage and scoped Compose cleanup passed.
- Independent `autplay_reviewer`: zero remaining Critical/Major after the fix/review loop. Rollback,
  same-transaction DB activation audit, tag conflicts, legacy/data downgrade guards, read-only
  model cache, runnable composition and job-key/target/model-task binding were corrected and
  covered. Unsupported standalone tag work is rejected instead of remaining queued forever. The
  real accelerator OOM/metrics gate remains an explicit acceptance blocker, and A-030 is not marked
  PASS.

## Acceptance state

- A-029 `PASS`: physical dependency/image/profile isolation and CPU/API independence.
- A-030 `IN_PROGRESS`: bounded handler OOM/retry/checkpoint/fence plus real-PostgreSQL dead-process
  lease recovery evidence passes; mandatory real CUDA OOM evidence needs the reviewed artifact and
  RTX host.
- A-031 `PASS`: additive schema and real-PostgreSQL A/B parallel storage, dimension/hash/conflict,
  owner ACL, switch and immediate rollback evidence.
- P12 `BLOCKED`: required real accelerator OOM and metric report are absent.

## Risks and debt

- R-007 is partially mitigated; no model may become ACTIVE until the external hardware/artifact
  gate supplies an immutable APPROVED report.
- ONNX Runtime/CUDA/cuDNN packages are isolated and exactly locked, but actual driver compatibility
  and peak-VRAM behavior remain unvalidated on the target server. The candidate weights/license
  are still absent and cannot be inferred from the runtime package.
- P14 must still produce SBOM/runtime-driver compatibility evidence for any eventually accepted
  GPU package and artifact.

## Git state

- Branch: `codex/autplay-harness-v1`.
- Starting/current recorded HEAD before phase close: `0023fa9ad9d12633ad988230662fbd69bb74eb20`.
- The worktree already contained extensive uncommitted P04-P11/harness/user changes. They were
  preserved. No commit, push, PR, deployment, external write or real-data migration was performed.

## Exact next prerequisite

Do not start P13 as a continuation of this blocked handoff. Resume P12 on the personal NVIDIA
server with a reviewed artifact. Run `scripts/test-p12-gpu.ps1` using `auto` and, if desired, the
chosen UUID/PCI selector; configure the eligible registry UUID and private hash-addressed artifact,
then force real process stop/restart and CUDA OOM, record same-dataset
tracks/hour/p95/peak-VRAM/quality metrics, store an
immutable APPROVED or EXPERIMENTAL report, rerun both canonical gates and update this handoff.

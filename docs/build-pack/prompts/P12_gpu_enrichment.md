# P12 - Isolated GPU Enrichment and Embeddings

Выполни только phase P12. Следуй common protocol и прочитай `HANDOFF_P11.md`.

## Цель

Использовать RTX 3060 12 GB как optional isolated enrichment worker: versioned audio/text embeddings and tags, benchmarked against CPU baseline, without coupling core ingest/API readiness to GPU.

## Inputs

- System Architecture GPU/model registry sections
- Product GPU/recommendation requirements
- PostgreSQL embedding/model/job schema
- P11 evaluation baseline
- `REFERENCE_PROJECTS.md` pgvector section
- `docs/design/AutPlay_Recommendation_Subsystem_v1.md` embedding read/write and model-boundary rules

## Scope

1. `autplay-ml-gpu` process/container profile separate from API/CPU worker.
2. Approved Model Registry: source, license, revision, SHA-256, preprocessing, dimensions, runtime and status.
3. Bounded audio segment decoder and deterministic preprocessing.
4. Candidate model experiment plan suited to 12 GB VRAM; choose no more than necessary.
5. Versioned embedding/tag jobs, checkpoints, retry and terminal failure states.
6. Parallel embedding rows per model version; no in-place overwrite.
7. Exact pgvector retrieval and exact re-score baseline.
8. HNSW only if recorded data proves exact search misses latency target; include recall comparison and rollback.
9. Shadow comparison against P11: relevance/diversity/novelty/repeat/latency/throughput/VRAM.
10. Active model switch, rollback window and derived-data retirement.
11. P11 consumes only `TrackEmbeddingReader`; the isolated worker owns `TrackEmbedder` and
    `TrackEmbeddingWriter`. Embedding/model changes leave the public recommendation and interaction
    contracts unchanged.

## Constraints

- API/CPU package cannot import CUDA runtime or load weights.
- Arbitrary model URL/job payload forbidden.
- Gated/private/large model download requires explicit authorization and license review.
- Record immutable model/artifact hashes.
- One heavy GPU job concurrently until benchmark proves safe.
- OOM: bounded batch reduction, then deferred/terminal outcome; never API outage.
- Core ingest completes before ML enrichment.
- Shadow models read the same versioned interaction dataset and never emit duplicate impressions.
- Interaction capture and CPU recommendation request logging remain available during every GPU/model
  failure.
- Evaluation datasets record interaction-schema version and snapshot watermark; tensors, semantic
  IDs and raw model features never enter the interaction wire schema.

## Required tests/evidence

- CPU-only Compose starts with GPU profile absent;
- GPU worker unavailable/restarted mid-job;
- forced OOM and retry bounds;
- deterministic preprocessing/version hash;
- wrong dimension/model hash rejected;
- model A/B parallel storage and switch/rollback;
- no cross-user ACL leakage in retrieval;
- exact vs approximate recall if HNSW proposed;
- RTX 3060 report: tracks/hour, p95 job time, peak VRAM, quality delta.

## Acceptance

GPU path demonstrably improves a measured metric or remains experimental without replacing CPU baseline. Core readiness and playback stay green when GPU is stopped.

Create `HANDOFF_P12.md`, update A-029..A-031 and stop.

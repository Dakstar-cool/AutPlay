# ADR-025: Isolated GPU enrichment, device selection and model rollout

- Status: Accepted under the standing technical-decision authorization
- Date: 2026-08-17
- Phase: P12

## Context

AutPlay is developed on a CPU-only/AMD laptop, while the personal server is expected to start with
an NVIDIA RTX 3060 12 GB and may later receive a different or additional accelerator. P12 must not
make API, ingest, playback or the P11 recommendation baseline depend on that hardware. Model
artifacts are executable supply-chain inputs and cannot be accepted by URL in a generic job.

The current development host has only `AMD Radeon(TM) Graphics`; `nvidia-smi` is unavailable. A
real RTX throughput/VRAM/quality report therefore cannot be produced on this host.

## Decision

1. `gpu/` is a separate uv project with its own lock and Dockerfile. `server/`, its lock, the CPU
   image and the default/runtime-only Compose path contain no CUDA/ML framework packages and never
   import `autplay_gpu`.
2. The optional `ml-gpu` Compose service has profile `gpu`, no published port, a read-only Vault
   mount and a read-only private model-cache mount. Runtime startup uses both `runtime` and `gpu` profiles;
   API/CPU services have no dependency on `ml-gpu`.
3. Hardware discovery is an `AcceleratorInventory` boundary. P12 implements bounded NVIDIA
   discovery via `nvidia-smi`; adding another vendor requires only another inventory/runtime
   adapter. `auto` filters incompatible devices and orders by compute capability, total VRAM, free
   VRAM and UUID. Operators may select `uuid:<id>`, `pci:<id>` or `index:<n>`. UUID/PCI are the
   durable choices; display names are rejected as ambiguous. The selected UUID and full inventory
   snapshot belong in every benchmark report.
4. Approved models start as `BENCHMARK`. Registry provenance includes source/revision, artifact
   filename/format/size, RFC 8785 manifest hash, weights hash, preprocessing manifest/hash,
   license and review reference, runtime revision, precision, dimensions and lifecycle status.
   Generic job payloads carry only an `enrichment_job_id`; paths and arbitrary URLs are forbidden.
5. The GPU project pins `onnxruntime-gpu[cuda,cudnn]` 1.26.0 in its separate lock and allowlists a
   single-input/single-output float32 waveform-to-embedding ONNX adapter. The adapter binds the
   selected device index and requires `CUDAExecutionProvider`; the worker verifies model registry
   eligibility plus a private hash-addressed artifact before constructing it or claiming a job.
   This runtime choice does not approve or bundle any model weights.
6. Enrichment uses bounded deterministic FFmpeg mono float32 segments, immutable input/vector
   hashes, lease fencing, idempotent writes and bounded batch halving on classified OOM. Model
   versions produce parallel rows. Switching requires an immutable `APPROVED` benchmark and keeps
   append-only rollback evidence; derived retirement is bounded and forbidden during a rollback
   window.
   The executable job key is currently only `ml.audio-embedding/v1`. Domain, handler, writer and
   database checks bind that key to target/model task `AUDIO_EMBEDDING`. Versioned tag rows may be
   emitted atomically by such a reviewed model, but standalone `AUDIO_TAGS` jobs are rejected until
   a concrete handler is implemented; unsupported durable work must never remain queued forever.
7. Retrieval remains an exact pgvector cosine query over the requesting owner's authorized,
   committed and available Vault set, followed by a stable Recording-ID tie-break. No HNSW index
   is added because no target-scale measurement currently shows an exact-search latency miss.
8. P11 public recommendation/interaction contracts and CPU serving remain unchanged. Shadow
   models use the same versioned P11 dataset identity and never create impressions.

## Candidate experiment

One candidate family is sufficient initially: a reviewed LAION-CLAP music checkpoint, because CLAP
provides aligned audio/text representations and publishes music-specific checkpoints. The project
is recorded only as a candidate, not an approved artifact: the exact checkpoint license/provenance,
hash, runtime compatibility and RTX 3060 measurements still require review. The code/project is
reported under CC0, but that does not automatically approve every training input or checkpoint for
AutPlay. Primary records: [LAION-CLAP repository](https://github.com/LAION-AI/CLAP) and
[CLAP paper](https://arxiv.org/abs/2206.04769).

## Consequences

- Hardware upgrades do not change the CPU application or database contracts; `auto` can pick a
  stronger compatible GPU, while UUID/PCI provides explicit control. RTX 3060 / GA106 / compute
  capability 8.6 / 12 GB describes only the current measurement target. Application compatibility
  is expressed as configurable minimum capabilities, never that product name or architecture.
- Missing NVIDIA hardware or a missing/unreviewed artifact fails only the optional worker. The CPU
  baseline remains authoritative.
- A029 and A031 are proven by isolation and database tests. A030 has bounded handler/checkpoint
  plus real-PostgreSQL lease-expiry/process-restart evidence, but remains in progress until the
  configured process is forced through real accelerator OOM. P12 cannot be declared fully green until a selected
  RTX-class device also supplies tracks/hour, p95 job time, peak VRAM and quality delta. The laptop
  UNAVAILABLE report is retained instead of fabricated metrics.
- Exact retrieval is the rollback-safe baseline. HNSW remains absent until a named dataset and
  hardware breach the target and a recall comparison justifies it.

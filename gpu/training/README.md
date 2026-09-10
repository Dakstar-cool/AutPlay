# AutPlay Sona-Lite training

This uv project is the training-only boundary for the learned Semantic-ID tokenizer, shared
encoder/decoder/ranker and deterministic ONNX export. It is not installed in the CPU server or the
runtime-only GPU worker image.

Canonical local checks:

```powershell
uv sync --frozen
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest -q
```

Linux resolves the exact PyTorch CUDA 13.0 build and the CUDA/cuDNN ONNX Runtime distribution;
Windows development resolves the exact CPU builds. The mutually exclusive environment markers
keep one ONNX Runtime distribution in each environment. The exported ONNX artifact remains subject
to the existing approved-model registry, hash verification and RTX benchmark gates before shadow
execution.

The bounded pipeline refuses to overwrite datasets, checkpoints, exports, or benchmark evidence.
Datasets contain fixed tensors and example hashes only; raw owner and recording identifiers are not
materialized. A keyed owner-lineage token remains so an authorized privacy deletion can locate the
derived artifact without revealing the owner UUID. The tokenizer verifies the canonical
recording-ID/float32 embedding snapshot hash and uses bounded deterministic residual mini-batch
k-means. Checkpoints use individually hashed NumPy arrays instead of pickle. The ONNX sidecar binds
the graph and weights to the exact dataset, tokenizer, checkpoint, and model configuration; a final
commit marker is required before the graph/sidecar pair is considered published. The model decoder
vocabulary must be exactly the tokenizer's active centroid range plus reserved code zero, so a
successful benchmark cannot emit an unexpandable Semantic ID.

For a non-quality hardware smoke, create an explicitly synthetic bundle and train a compact model:

```powershell
uv run --frozen autplay-sona-training fixture `
  --output artifacts/fixture `
  --recording-count 1
uv run --frozen autplay-sona-training train `
  --dataset artifacts/fixture/dataset `
  --checkpoint artifacts/checkpoint `
  --device cuda `
  --epochs 1 `
  --batch-size 8 `
  --model-dimensions 16 `
  --encoder-layers 1
uv run --frozen autplay-sona-training export `
  --checkpoint artifacts/checkpoint `
  --output artifacts/sona-lite.onnx
uv run --frozen autplay-sona-training benchmark `
  --checkpoint artifacts/checkpoint `
  --dataset artifacts/fixture/dataset `
  --tokenizer artifacts/fixture/tokenizer `
  --output artifacts/pytorch-gpu-smoke.json `
  --device cuda
```

Synthetic bundles and everything derived from them carry `quality_eligible=false`. They prove the
toolchain and hardware route only; they cannot satisfy the reviewed-model, recommendation-quality,
or serving gates.

## Quality-candidate path

The quality commands accept only an independently signed, non-synthetic train/validation/test
bundle. The reviewer key is not a CLI argument: deployment must provide an absolute, non-symlink
SPKI path through `AUTPLAY_SONA_QUALITY_REVIEWER_SPKI_PATH` and pin its lowercase SHA-256
thumbprint through `AUTPLAY_SONA_QUALITY_REVIEWER_THUMBPRINT_SHA256`.

Run `verify-quality-bundle`, then `train-quality`, `export-quality`, and `benchmark-quality` with
the same `--train-dataset`, `--validation-dataset`, `--test-dataset`, `--tokenizer`,
`--source-manifest`, `--catalog-manifest`, `--teacher-calibration`, `--teacher-manifest`,
`--source-rekey-plan`, `--source-provenance-acceptance`, `--source-approval`, and
`--dataset-approval` arguments. The source manifest V2 binds the exact re-key plan and the
content-addressed acceptance of `RECONSTRUCTED_FROM_0026_SYNC_TRUTH_V1`, including the fixed
deterministic replacement scheme for the unavailable Android-local `server_profile_id`; it never
represents reconstructed temporal data as an original persisted R1A snapshot. The teacher
calibration artifact contains
the complete validation request/candidate membership, masked labels, and raw frozen
`cpu-baseline:1` P11 logits. `SONA_P11_TEACHER_V2` binds that artifact and the deterministic
per-head temperature fit. Every quality-bundle verification re-fits all four temperatures and
requires the resulting float32 teacher probabilities, labels, and masks to match the validation
tensors exactly. Legacy or digest-only teacher evidence fails closed.

The commands re-verify the signed bundle at their use and publish boundaries. Training emits a
provenance-eligible candidate, not a quality-approved model. Export rechecks the exact checkpoint
weights before and after ONNX conversion. Benchmark executes an immutable byte snapshot of the
committed ONNX artifact against the approved test split and records the checkpoint, weights,
dataset, tokenizer, graph, manifest, commit, environment, and raw latency identities. The canonical
benchmark envelope contains exactly one raw `(request_sha256, iteration, latency_ms)` sample for
every test request and measured iteration; the server rejects missing, duplicated, substituted, or
summary-only benchmark evidence and recomputes mean, P50, and P95.
`benchmark-quality` is CUDA-only and the server requires `CUDAExecutionProvider`; CPU/automatic
fallback is valid only for non-quality smoke evidence. The quality session sets
`session.disable_cpu_ep_fallback=1` and disables the Python run-fallback path, so model loading or
execution fails instead of assigning unsupported nodes to CPU. A separate untimed profiling pass
must also report `CUDAExecutionProvider` for every executed ONNX node before the benchmark can set
`cuda_only_execution=true`; provider registration alone is not accepted as placement evidence.
The exporter keeps the upstream GRU mutation warning visible and tests eager/ORT numerical parity
for empty, single-item, and maximum history/candidate masks instead of suppressing that warning.

The server-side paired evaluator separately requires a signed canonical execution bundle containing
the persisted P11 request, baseline snapshot, complete P11 ranking, Sona request, and Shadow
evidence; canonical held-out outcomes, safety evidence, paired performance evidence, and the exact
ORT benchmark envelope; a signed evaluation-evidence approval; a passing semantic gate; and a final
signed artifact approval. It independently rechecks canonical snapshot/request/ranking hashes,
NumPy headers and payload sizes, and the ONNX model with the official ONNX checker. Only final
verification of that complete ancestry may derive `quality_eligible=true`; none of these steps
activates R1C serving.

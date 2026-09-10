# AutPlay isolated GPU worker

This uv project is intentionally separate from `server/`. Its lock, image and imports are not part
of the API/CPU worker dependency graph. It contains the pinned ONNX Runtime CUDA adapter and actual
durable enrichment-worker composition, but every model remains experimental until a reviewed
artifact and an on-server benchmark prove a quality or throughput benefit.

Device selection accepts `auto`, `uuid:<GPU UUID>`, `pci:<PCI bus ID>` or `index:<n>`. `auto`
filters incompatible devices and deterministically prefers compute capability, total VRAM, free
VRAM and then GPU UUID. Benchmarks record the complete selected-device snapshot.

The current server target is an RTX 3060 12 GB (GA106, compute capability 8.6), not an application
requirement. Compatibility defaults are capability-based (compute capability 7.0 and 4 GiB VRAM)
and may be changed with `AUTPLAY_GPU_MIN_COMPUTE_MAJOR`,
`AUTPLAY_GPU_MIN_COMPUTE_MINOR` and `AUTPLAY_GPU_MIN_MEMORY_MIB` without changing job or business
logic. A future compatible GPU is discovered automatically; UUID/PCI/index provides an override.

Production worker startup also requires:

- `AUTPLAY_GPU_MODEL_ID`, identifying a registry row in `BENCHMARK` or another eligible state with
  runtime `ONNX_RUNTIME_CUDA`, revision `1.26.0`, artifact format `ONNX` and precision `FP32`;
- the reviewed model bytes at
  `<AUTPLAY_GPU_MODEL_CACHE_ROOT>/objects/<sha256-prefix>/<weights-sha256>`;
- the ordinary worker database/Vault settings and a local verified Vault replica.

The worker verifies registry eligibility, artifact path/size/SHA-256 and CUDA provider binding
before claiming a job. Missing hardware, configuration or reviewed bytes fails only this optional
process. `--check-readiness` intentionally checks accelerator and database infrastructure without
loading a large model; use `--once` for one real durable worker tick.

Use `scripts/test-p12-gpu.ps1` for a host-installed `uv` workflow or
`scripts/test-p12-gpu.sh` on a Linux Docker/NVIDIA Container Toolkit host. The Docker gate builds
the isolated image and runs list, deterministic selection and configuration checks with no network,
no volumes, a read-only root filesystem and exact-name cleanup. PCI selectors accept both NVML's
legacy four-digit and current eight-digit domain forms.

## Sona-Lite shadow endpoint

Checkpoint R1B-5 adds an inference-only mode that is deliberately separate from the durable P12
worker:

```text
autplay-ml-gpu --serve-sona-shadow
```

It always binds `127.0.0.1` and exposes exactly `POST /internal/sona/v1/infer` plus the
identity-only readiness probe `GET /internal/sona/v1/ready`. Configure the port with
`AUTPLAY_GPU_SONA_BIND_PORT` and provide all three immutable identities:

- `AUTPLAY_GPU_SONA_ARTIFACT_SHA256` for the content-addressed ONNX bytes;
- `AUTPLAY_GPU_SONA_MODEL_MANIFEST_SHA256` for the canonical model manifest;
- `AUTPLAY_GPU_SONA_TOKENIZER_SHA256` for the exact Semantic-ID tokenizer used by the CPU server.

The artifact, `<artifact>.manifest.json` and `<artifact>.commit.json` must already exist under the
configured model cache. Startup verifies every hash before CUDA session creation. The endpoint has
strict request/response size and schema bounds. The canonical request carries the owner UUID needed
for end-to-end snapshot/hash binding, but the worker does not log or persist it, write recommendation
items or create impressions. Access from another host must terminate in an authenticated
SSH/Tailscale tunnel whose remote destination is still loopback.

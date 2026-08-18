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

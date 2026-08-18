# Disposable PostgreSQL, CPU runtime and optional P12 GPU profile

The base Compose file runs exactly one PostgreSQL 18 service with pgvector and publishes no host port. Its named volume is scoped by the Compose project and is intentionally disposable. `compose.test.yaml` is used only by the canonical check scripts: it assigns a random loopback-only host port so real migration, auth, job, and Vault crash/concurrency tests can connect, then the scripts remove the exact project container, network, and volume and verify that none remain.

`compose.runtime.yaml` adds the `runtime` profile: one-shot Alembic migration followed by separate API, CPU-worker, and direct-stream processes built from the same non-root CPU image whose base is digest-pinned. The worker image pins FFmpeg/FFprobe and Chromaprint/fpcalc, but the isolated stream process imports no worker or media-tool code and receives the Vault volume read-only. API and worker share the same writable Vault volume so staging and final publication remain in one filesystem atomicity domain. Healthchecks and ingest require no GPU or external Internet access. Both published ports default to loopback-only. The root allowlist `.dockerignore` limits the build context to the server lock, package source, and migration inputs so workspace secrets and caches are never uploaded to the builder.

Before parsing the runtime overlay, set `AUTPLAY_RUNTIME_AUTH_SECRET_FILE` to a local file outside the repository containing at least 32 random characters. Compose mounts it read-only; do not place a real credential in YAML, source control, shell history, or logs.

```text
docker compose -f deploy/compose/compose.yaml -f deploy/compose/compose.runtime.yaml --profile runtime up --build --wait
docker compose -f deploy/compose/compose.yaml -f deploy/compose/compose.runtime.yaml --profile runtime down --volumes
```

For an explicitly trusted LAN, set `AUTPLAY_RUNTIME_BIND_HOST` to the laptop's concrete LAN IPv4
address before running the same Compose command. Do not use `0.0.0.0`: binding a specific address
keeps VPN and other host interfaces out of scope. Restrict the host firewall rule to the two
published TCP ports and the local subnet. This development profile serves plain HTTP, so it must
not be exposed to an untrusted Wi-Fi network or the public Internet; those topologies require the
production TLS edge and deployment decisions.

`autplay_dev_only` is a fixed disposable development credential, not a deployable secret. These files are not production manifests and must never be pointed at real/user data. Production database roles, TLS/domain topology, secret delivery, backup/restore, and public networking require their owning later phase and explicit deployment approval.

P12 adds `ml-gpu` under the opt-in `gpu` profile. It is built from `gpu/Dockerfile`, publishes no
port, has no API/CPU dependency edge, reads Vault and pre-provisioned private model-cache bytes
read-only and writes only PostgreSQL derived state. The normal `runtime` command does not build or start it. On an
NVIDIA host enable both profiles:

```text
AUTPLAY_GPU_DEVICE_SELECTOR=auto
AUTPLAY_GPU_MODEL_ID=<reviewed registry UUID>
docker compose -f deploy/compose/compose.yaml -f deploy/compose/compose.runtime.yaml --profile runtime --profile gpu up --build
```

`AUTPLAY_GPU_DEVICE_SELECTOR` accepts `auto`, `uuid:<GPU UUID>`, `pci:<PCI bus ID>` or `index:<n>`.
Use `scripts/test-p12-gpu.ps1` to list and exercise the selection before starting the service. P12
contains the pinned ONNX Runtime CUDA process but no approved weights. It therefore exits
fail-closed with a stable configuration/artifact error before claiming work until an eligible
registry model and hash-addressed artifact are supplied. Process restart is bounded to three
failures. This does not change API, stream, playback, ingest or CPU-worker health. The current RTX
3060 12 GB is benchmark hardware, not a Compose or application requirement; `auto` evaluates the
detected GPU capabilities and explicit UUID/PCI/index selectors support multi-GPU and upgraded
servers.

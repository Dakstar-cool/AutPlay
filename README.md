# AutPlay

AutPlay is an Android-first, local-first music platform with an optional personal server. P01-P11 established the reproducible foundation through the deterministic CPU recommendation baseline. P13 adds authenticated Hybrid Wave group playback with durable PostgreSQL room/command truth, per-device source preflight, Room v10 recovery projections, Media3 scheduled execution and bounded clock/drift recovery. P14 supplies the completed local RC build, restore, security, dependency, performance, physical Samsung A55, test-evidence and operations package. ADR-027 explicitly defers only P12 real RTX/model evidence, keeps the CPU baseline authoritative and activates no GPU model. Wave evidence remains limited to the declared trusted-local, single-API-process topology; public Internet/TLS and cross-instance fanout remain deferred. No publication, deployment or production signing has been performed or authorized.

## Pinned prerequisites

- `uv 0.12.3`; it installs the pinned CPython `3.14.7` runtime.
- Microsoft OpenJDK `17.0.20+8-LTS` with `JAVA_HOME` set.
- Android SDK platform `36.1`, Build Tools `36.1.0`, and `ANDROID_HOME` set.
- Docker Engine with a Docker Compose release that supports `up --wait` (Compose `5.2.0` is the recorded P01 host observation).

The committed Gradle wrapper downloads Gradle `9.3.1` and verifies the distribution checksum. The host Android gate builds debug and minified release APKs without a device. P05 connected evidence uses an API 26 x86_64 AVD; WSL needs its own Linux JDK and Android SDK, because a mounted Windows SDK path is not portable evidence.

## Canonical commands

Run commands from the repository root. These scripts are the single source of truth for local and CI bootstrap/check sequencing.

Windows PowerShell:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\check.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\check.ps1 -ServerOnly
```

Linux, macOS, or a provisioned WSL environment:

```bash
bash scripts/bootstrap.sh
bash scripts/check.sh
bash scripts/check.sh --server-only
```

`bootstrap` installs the pinned Python with uv and performs separate frozen syncs for the root development tooling and the CPU-only server. It then resolves the Gradle wrapper and validates Compose configuration. `check` repeats the frozen bootstrap, verifies both locks, validates the language-neutral contracts, runs the harness and server import/Ruff/format/mypy/pytest gates, audits only the server CPU dependency graph, runs Android lint/host-unit/debug/minified-release-R8 gates unless server-only, then owns a uniquely named disposable PostgreSQL project. It publishes PostgreSQL only on a random loopback port, verifies PostgreSQL/pgvector versions, runs the complete server suite including P06 Vault and P09 sync concurrency/crash/reset/compaction evidence, and removes the test container, network, and volume. `ServerOnly`/`--server-only` skips Android but still validates contracts and requires Docker because persistence and runtime repository tests use the real database.

The device-independent P04 gate and the current connected Android gate can also be run directly:

```powershell
uv run --frozen pytest tests/contract
$gradleJavaHomeArgument = "-Dorg.gradle.java.home=$env:JAVA_HOME"
.\gradlew.bat $gradleJavaHomeArgument --no-daemon --console=plain :apps:android:connectedDebugAndroidTest
```

The connected command requires a booted API 26+ emulator or device. See [`docs/implementation/CI_PLAN.md`](docs/implementation/CI_PLAN.md) for the hosting-neutral job matrix and [`docs/implementation/HANDOFF_P13.md`](docs/implementation/HANDOFF_P13.md) for the current evidence state.

## Codex development harness

The repository-level harness is a local development tool; it is not part of the AutPlay server image or product runtime. Its root `uv.lock` is intentionally separate from `server/uv.lock`, so the official `openai-codex` SDK and harness test dependencies cannot enter the CPU server graph.

```powershell
uv run --frozen autplay-codex status
uv run --frozen autplay-codex task "Describe one bounded repository task" --dry-run
uv run --frozen autplay-codex next
uv run --frozen autplay-codex milestone P04 --dry-run
uv run --frozen autplay-codex review
uv run --frozen autplay-codex resume
uv run --frozen autplay-phase-stop validate
uv run --frozen autplay-phase-stop init --from-phase P04
```

`task`, `next`, and `milestone` refuse `main`/`master` and any dirty worktree; commit or stash existing work before automated writes. When JSON `next` has already selected an eligible task but Git safety refuses execution, its bounded response retains the selected task, a null decision blocker and `execution_started: false` without exposing raw Git details. The commands also reject destructive, production-deploy, protected-merge, force-push, reset/restore, and recursive-delete requests, including command-shaped and Russian-language variants covered by the policy. `--model`, `--reasoning`, and the persisted-goal flags are explicit overrides. `ultra` is deliberately not a reasoning value: checked-in configuration is limited to `minimal`, `low`, `medium`, `high`, and `xhigh`.

Routing chooses Luna for clear mechanical work, Terra for ordinary engineering, and Sol for risky cross-module work; milestones use Sol/xhigh and a durable goal definition/status/checkpoint record. State and allowlisted event logs live under ignored `.autplay-codex/`; writes use an operation lease, revision CAS, file lock, and atomic replacement. Resume checks repository, branch, and last observed HEAD. Reviewer turns are read-only; unresolved critical/major standalone findings become a durable blocked task and backlog state. Implementation/review/fix loops and streamed command output are bounded, and a timeout terminates the isolated command process tree. The authoritative product phase state remains [`docs/implementation/PLAN.md`](docs/implementation/PLAN.md); [`AUTPLAY_CODEX_BACKLOG.json`](docs/implementation/AUTPLAY_CODEX_BACKLOG.json) is only its machine-readable companion. Backlog eligibility is revalidated and terminal status is synchronized under the same operation lease, including after resume, without rerunning a terminal task. P00-D006 Variant A and P00-D006-R1 are accepted in [`P00-D006_AGGREGATE_ID_MAPPING.md`](docs/implementation/P00-D006_AGGREGATE_ID_MAPPING.md), so `next` reports P04 as eligible without starting it or skipping ahead.

The trusted project-local `Stop` hook reads [`AUTPLAY_CODEX_PHASE_PIPELINE.json`](docs/implementation/AUTPLAY_CODEX_PHASE_PIPELINE.json), not the last model response. Setup initializes the ignored state exactly once with `init`; the command refuses overwrite, a non-green source backlog, or a non-queued successor. For an initialized edge the hook verifies the declared artifacts and mandatory argument-vector gates, atomically records the source phase as `completed`, the successor as `started`, and the transition as consumed under ignored `.autplay-codex/phase-pipeline/`, then returns one `decision: "block"` continuation. `stop_hook_active`, a red gate, missing/corrupt state, or an already consumed edge produces no continuation. Adding a later manifest edge reuses the durable active-phase state without changing the Python orchestrator. P04 -> P05 is pre-authorized and requires no additional phase confirmation; frozen decisions and documented security/data stop conditions still apply. Codex requires a one-time trust review for a new or changed project hook via `/hooks`.

See [`AUTPLAY_CODEX_HARNESS_V1.md`](docs/implementation/AUTPLAY_CODEX_HARNESS_V1.md) and [`HANDOFF_HARNESS_PHASE_PIPELINE_V1.md`](docs/implementation/HANDOFF_HARNESS_PHASE_PIPELINE_V1.md) for contracts, safety boundaries, and verification evidence.

## Optional P12 GPU project

`gpu/` has its own uv lock and image; canonical CPU bootstrap/check commands do not install, build,
pull or start it. On an NVIDIA server, the standalone gate inventories compatible devices and tests
`auto` or an explicit stable selector:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\test-p12-gpu.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\test-p12-gpu.ps1 `
  -DeviceSelector 'uuid:GPU-...'
```

Selectors are `auto`, `uuid:<GPU UUID>`, `pci:<PCI bus ID>` and `index:<n>`. Prefer UUID or PCI for
a durable manual choice; `auto` deterministically selects the strongest compatible visible GPU.
Starting the optional service requires both profiles and a reviewed private model artifact:

```powershell
$env:AUTPLAY_GPU_DEVICE_SELECTOR = 'auto'
$env:AUTPLAY_GPU_MODEL_ID = '<reviewed registry UUID>'
$env:AUTPLAY_RUNTIME_AUTH_SECRET_FILE = 'C:\path\outside\repo\autplay-auth-secret.txt'
docker compose -f deploy/compose/compose.yaml -f deploy/compose/compose.runtime.yaml `
  --profile runtime --profile gpu up --build
```

The image contains a pinned ONNX Runtime CUDA adapter. It deliberately fails closed before claiming
a job while no eligible registry model or hash-addressed reviewed artifact is configured; API,
playback and the CPU worker remain independent. The RTX 3060 12 GB is only the current benchmark
target: compatibility is selected from detected capabilities and can follow a future GPU upgrade
without changing application business logic. See
[`ADR-025`](docs/adr/ADR-025-p12-isolated-gpu-enrichment-and-model-rollout.md) and
[`HANDOFF_P12`](docs/implementation/HANDOFF_P12.md).

## Disposable P06 runtime

The runtime overlay starts migration, API, CPU-worker, and isolated direct-stream processes on the same CPU-only image. API and worker share the writable Vault volume; stream receives it read-only. FFmpeg/FFprobe and Chromaprint/fpcalc are exact-version and checksum/digest pinned. Supply a local development signing-secret file containing at least 32 random characters through `AUTPLAY_RUNTIME_AUTH_SECRET_FILE`; keep that file outside the repository. API and stream ports are published only on loopback.

The deterministic media-tool acceptance command builds the pinned image, disables container networking, full-decodes/fingerprints a generated FLAC, rejects corrupt/metadata-shaped fixtures and proves recoverable quarantine:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\test-p06-media-runtime.ps1
```

```powershell
$env:AUTPLAY_RUNTIME_AUTH_SECRET_FILE = 'C:\path\outside\repo\autplay-auth-secret.txt'
docker compose -f deploy/compose/compose.yaml -f deploy/compose/compose.runtime.yaml --profile runtime up --build --wait
docker compose -f deploy/compose/compose.yaml -f deploy/compose/compose.runtime.yaml --profile runtime down --volumes
```

```bash
export AUTPLAY_RUNTIME_AUTH_SECRET_FILE=/path/outside/repo/autplay-auth-secret.txt
docker compose -f deploy/compose/compose.yaml -f deploy/compose/compose.runtime.yaml --profile runtime up --build --wait
docker compose -f deploy/compose/compose.yaml -f deploy/compose/compose.runtime.yaml --profile runtime down --volumes
```

These commands are for disposable local development only. See [`deploy/compose/README.md`](deploy/compose/README.md) for boundaries and probes.

## Repository boundaries

| Path | Current content |
| --- | --- |
| `apps/android` | Offline Compose library/search/playlist/history/import-review/player/sync-status/Home flows, Room v9 profile/owner-scoped projections, verified recommendation packs and presentation mapping, bounded WorkManager/OkHttp sync, FTS5, SAF validation, DataStore/Keystore, Media3 playback/session/download services and separate caches |
| `server/src/autplay` | Domain/application/port boundaries; typed owner-scoped PostgreSQL persistence; library, sync, import/identity, model-independent CPU recommendations and framework-free enrichment contracts; CPU-only API/worker/stream plus crash-safe filesystem Vault |
| `gpu` | Separate optional P12 uv/image boundary: NVIDIA inventory and selection, verified Vault/model artifacts, deterministic FFmpeg preprocessing, pinned ONNX CUDA adapter and durable GPU-worker composition |
| `server/migrations` | Linear Alembic revisions `0001` through `0015`, including immutable P12 model provenance and durable P13 Wave room/command state |
| `tools/autplay_codex` | Local Codex SDK orchestration, routing, state, safety, checks, and tests |
| `.codex`, `.agents/skills` | Project Codex configuration, custom agents, and repository workflow skill |
| `contracts/openapi`, `contracts/events` | P04 OpenAPI 3.1 source and Draft 2020-12 Sync Protocol v1 schemas, including canonical listening/impression/feedback events |
| `deploy/compose` | Digest-pinned PostgreSQL 18 + pgvector base, loopback-only test override, disposable CPU runtime profile and isolated opt-in GPU profile |
| `tests/contract`, `tests/fixtures` | Device-independent contract validators, sync/interaction golden vectors, bounded import fixtures and immutable recommendation evaluation fixtures |
| `docs/adr` | Accepted toolchain, dependency-boundary, identity-history, playback and sync-runtime decisions |

Development Compose data is disposable and must never contain real/user data. P12 adds no broker/cache/microservice, separate vector database, external model downloader, live metadata provider, automatic identity activation or production deployment topology. The repository still does not provide production TLS/domain/role topology, public registration, password credential persistence, external providers, an approved final recommendation model or production deployment approval.

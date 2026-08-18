<p align="center">
  <img src="./assets/readme/hero.svg" width="100%" alt="AutPlay — a local-first Android music platform extended by an optional personal server">
</p>

<p align="center">
  <strong>Keep listening offline. Add your own server when you want durable sync, an immutable music Vault, and shared playback.</strong>
</p>

<p align="center">
  <a href="#what-works-today">What works</a> ·
  <a href="#the-android-experience">Android UI</a> ·
  <a href="#how-autplay-works">How it works</a> ·
  <a href="#run-the-repository">Run the repository</a> ·
  <a href="#release-boundary">Release boundary</a>
</p>

## Music that does not wait for a server

AutPlay is an Android-first, local-first music platform. The Android app owns its working library, playlists, queue, playback state, downloads, and pending changes. A local action commits immediately; losing the network does not roll it back.

Connect an optional personal server to add device sync, an immutable filesystem Vault, authenticated Range streaming, resumable imports, deterministic recommendations, and trusted-local Hybrid Wave rooms. The CPU path remains complete without CUDA, a cloud account, or an external analytics service.

| Standalone Android | With a personal server |
| --- | --- |
| Library, ranked local search, duplicate-preserving playlists, history, queue, playback, downloads, and import review | Device sync, PostgreSQL-backed shared state, immutable Vault storage, streaming, backup/restore, recommendations, and Wave coordination |
| Room is the local source of truth | PostgreSQL is the server metadata, event, and job source of truth |
| Media3 owns playback and download execution | The server is optional; it never sits in the synchronous path of a local action |

## What works today

AutPlay is at a **local RC1 evidence boundary**, not a published production release. The repository contains executable proof for the CPU/local-first path:

| Proof | Verified result |
| --- | --- |
| Android runtime | RC gate: 82/82 on API 26. Post-RC frontend/server gate: 89/89 on a physical Samsung SM-A556E, installed side-by-side without clearing the existing app |
| Server and persistence | 425 server tests plus one documented Windows symlink-privilege skip against PostgreSQL 18.4 and pgvector 0.8.6 |
| Joined offline-to-online flow | File-backed Room → production OkHttp transport → authenticated FastAPI → PostgreSQL → second Room database, including post-commit ACK loss and exactly-once replay |
| Large-fixture search | PostgreSQL 100,000-row search p95 **6.403 ms**; Android 10,000-row FTS p95 **12.555 ms** |
| Recovery and security | Isolated PostgreSQL/Vault restore drill, object-authorization review, zero-finding production-source secret scan, pinned inventories, and CycloneDX SBOMs |

Start with the [RC1 test evidence](docs/release/TEST_EVIDENCE.md), [security review](docs/release/SECURITY_REVIEW.md), and [performance report](docs/release/PERFORMANCE_REPORT.md). Every acceptance claim is mapped to an executable evidence path in the [MVP acceptance matrix](docs/build-pack/MVP_ACCEPTANCE_MATRIX.md).

## The Android experience

<p align="center">
  <img src="./assets/readme/adaptive-frontend.svg" width="100%" alt="AutPlay adapts from compact bottom navigation to rail-based tablet and foldable layouts while keeping the player reachable">
</p>

The post-P14 frontend milestones turn the delivered feature set into one adaptive Compose surface:

- **Compact phones:** Home, Search, Library, Wave, and More remain in the bottom navigation; the mini-player stays reachable above it.
- **Foldables and landscape phones:** at `600dp`, navigation moves to a rail and gives content more horizontal room.
- **Tablets and expanded windows:** at `840dp`, the same rail renderer receives a wider content canvas; a distinct multi-pane renderer remains follow-up work.
- **Persistent interaction:** search results start durable queues; the player exposes seek, shuffle, repeat, like, and dislike; Wave exposes contract-backed create, join, share, preflight, host, and leave actions.
- **User-controlled appearance:** system, light, and dark modes with Coral, Violet, Green, and Blue accents; non-secret preferences remain device-local and exportable without credentials or private server data.
- **Optional server surfaces:** separate API/stream origins, online library snapshots, durable remote imports and Vault upload intents, online recommendations/replay, and explicit session-management actions without making local playback depend on the network.

The full Android host unit suite, lint/debug build, Android-test compilation, and 89/89 connected tests pass on a physical Samsung SM-A556E. Live local-server smoke covers API/stream health, library snapshot, online recommendations, and a durable import across app restart; see [HANDOFF_FRONTEND_M2_SERVER_SURFACES.md](docs/implementation/HANDOFF_FRONTEND_M2_SERVER_SURFACES.md).

## How AutPlay works

<p align="center">
  <img src="./assets/readme/system-map.svg" width="100%" alt="AutPlay commits Android state and an Offline Journal event locally, then synchronizes with an optional server when online">
</p>

One Android transaction commits the domain change and its durable Offline Journal intent. Media3 can keep playing from a readable local source while WorkManager retries deferred synchronization. When connected, the server applies idempotent events, returns bounded projections, and authorizes every Vault read independently—knowing a SHA-256 is never authorization.

The system keeps five concepts deliberately separate:

- **Recording** — the musical identity under review.
- **ReleaseTrack** — a recording in a release position.
- **UserTrackRef** — one user's intent and relationship to that identity.
- **AudioVariant** — a particular encoded representation.
- **VaultObject** — immutable verified bytes addressed by SHA-256.

Uncertain identity evidence stays unresolved or enters explicit review. AutPlay does not silently merge two recordings because their metadata or fingerprint looks similar.

## Product slices

### Local library and playback

- Offline library mutations, Cyrillic/Latin FTS5 search, playlists with duplicates and stable order, history, and repairable MediaStore/SAF references.
- Media3 session, queue recovery after process death, local-first source selection, authenticated Vault fallback, and durable offline downloads.
- Cached recommendation packs verified by exact bytes, owner, version, and expiry before presentation.

### Personal server and Vault

- CPU-only FastAPI runtime with owner/device sessions, redacted structured logs, health/readiness endpoints, and bounded PostgreSQL jobs.
- Resumable upload, full decode validation, versioned fingerprint evidence, immutable filesystem CAS, quarantine, reconciliation, and owner-authorized HTTP Range streaming.
- Idempotent push/ACK/pull sync with opaque cursors, tombstones, visible conflicts, bootstrap/reset, and pending-intent preservation.

### Import, recommendations, and Wave

- Bounded CSV/JSON/HTML imports with checkpoints, reports, shadow-only candidate evidence, and explicit offline review.
- Replaceable deterministic CPU recommendation pipeline with immutable input/pipeline provenance, exact replay, mandatory availability/ACL filters, and verified offline packs.
- Trusted-local Hybrid Wave rooms with durable PostgreSQL command truth, per-device source preflight, snapshot-first recovery, and scheduled Media3 execution.

## Run the repository

AutPlay currently ships as a reproducible development repository and local release candidate. It does not provide a public installer, production container registry, or deployment topology.

### Pinned prerequisites

- `uv 0.12.3`; it installs the pinned CPython `3.14.7` runtime.
- Microsoft OpenJDK `17.0.20+8-LTS` with `JAVA_HOME` set.
- Android SDK platform `36.1`, Build Tools `36.1.0`, and `ANDROID_HOME` set.
- Docker Engine with Docker Compose supporting `up --wait` (Compose `5.2.0` is the recorded host observation).

The committed Gradle wrapper downloads Gradle `9.3.1` and verifies its distribution checksum.

### Canonical commands

Run from the repository root. These scripts are the single source of truth for bootstrap and check sequencing.

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

`bootstrap` performs separate frozen syncs for repository tooling and the CPU-only server, resolves the Gradle wrapper, and validates Compose configuration. `check` repeats the frozen bootstrap, validates contracts, runs the harness and server quality gates, audits the CPU dependency graph, runs Android host gates unless server-only, and owns a uniquely named disposable PostgreSQL project. The disposable database is published only on a random loopback port and is removed with its network and volume after the run.

The connected Android gate requires a booted API 26+ emulator or device:

```powershell
uv run --frozen pytest tests/contract
$gradleJavaHomeArgument = "-Dorg.gradle.java.home=$env:JAVA_HOME"
.\gradlew.bat $gradleJavaHomeArgument --no-daemon --console=plain :apps:android:connectedDebugAndroidTest
```

See the [CI plan](docs/implementation/CI_PLAN.md) for the hosting-neutral matrix and the [P14 handoff](docs/implementation/HANDOFF_P14.md) for the local RC evidence.

<details>
<summary><strong>Start the disposable CPU runtime</strong></summary>

The runtime overlay starts migration, API, CPU worker, and isolated direct-stream processes on the same CPU-only image. Supply a development signing-secret file containing at least 32 random characters and keep it outside the repository. API and stream ports bind only to loopback.

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

The deterministic media-tool acceptance command builds the pinned image, disables container networking, validates a generated FLAC, rejects hostile/corrupt fixtures, and proves recoverable quarantine:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\test-p06-media-runtime.ps1
```

Read the [runtime Compose guide](deploy/compose/README.md) before using the profile.

</details>

<details>
<summary><strong>Inspect the optional GPU project</strong></summary>

`gpu/` has its own lock and image. Canonical CPU commands do not install, build, pull, or start it. The checked-in worker fails closed before claiming work unless a reviewed, hash-addressed model artifact and eligible registry row are configured.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\test-p12-gpu.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\test-p12-gpu.ps1 `
  -DeviceSelector 'uuid:GPU-...'
```

Selectors are `auto`, `uuid:<GPU UUID>`, `pci:<PCI bus ID>`, and `index:<n>`. Prefer UUID or PCI for a durable manual choice. No GPU model is packaged or active in RC1; the deterministic CPU recommendation baseline remains authoritative. See [ADR-025](docs/adr/ADR-025-p12-isolated-gpu-enrichment-and-model-rollout.md) and the [P12 handoff](docs/implementation/HANDOFF_P12.md).

</details>

<details>
<summary><strong>Use the repository-local Codex development harness</strong></summary>

The harness is development tooling, not part of the AutPlay runtime. Its root lock is intentionally separate from `server/uv.lock`.

```powershell
uv run --frozen autplay-codex status
uv run --frozen autplay-codex task "Describe one bounded repository task" --dry-run
uv run --frozen autplay-codex next
uv run --frozen autplay-codex milestone P04 --dry-run
uv run --frozen autplay-codex review
uv run --frozen autplay-codex resume
uv run --frozen autplay-phase-stop validate
```

Write-capable commands refuse protected branches, dirty worktrees, destructive requests, and unbounded execution. Product phase truth remains in the [implementation plan](docs/implementation/PLAN.md); harness state under `.autplay-codex/` is local runtime state, not product evidence.

</details>

## Repository map

| Path | Responsibility |
| --- | --- |
| `apps/android` | Adaptive Compose UI, Room v11 projections and Journal, Media3 playback/downloads, sync, import review, recommendations, server surfaces, and Wave recovery |
| `server/src/autplay` | Domain/application/port boundaries, CPU-only API/worker/stream composition, PostgreSQL adapters, Vault, sync, import, recommendations, and Wave |
| `server/migrations` | Linear Alembic revisions `0001` through `0015`; no destructive migration fallback |
| `gpu` | Physically separate optional NVIDIA/ONNX enrichment worker and exact dependency graph |
| `contracts` | OpenAPI 3.1 and Draft 2020-12 event contracts for sync, interactions, and Wave |
| `deploy/compose` | Digest-pinned PostgreSQL base plus loopback-only disposable runtime profiles |
| `tests` | Language-neutral contract vectors, import fixtures, and joined evidence scaffolding |
| `docs/release` | RC checklist, test evidence, security review, performance report, SBOMs, and release notes |

The server is a modular monolith. PostgreSQL owns shared metadata, sync, and job state; the filesystem/NAS boundary owns Vault bytes. Redis, RabbitMQ, Kafka, S3, and a separate vector database are not hidden dependencies.

## Release boundary

RC1 and the post-RC frontend milestone are deliberately honest about what they do **not** claim:

- No production signing, publication, pushed image, deployment, public domain/TLS topology, or production backup target has been selected.
- P12 real CUDA OOM, RTX throughput/job-time/VRAM, and reviewed-model quality evidence is `DEFERRED_WITH_APPROVAL` under [ADR-027](docs/adr/ADR-027-p14-conditional-phase-reachability.md). No GPU model is active.
- Wave evidence is limited to the declared trusted-local, single-API-process topology. Public Internet qualification and cross-instance live fanout remain deferred.
- Automatic probabilistic Recording matching remains disabled. Ambiguous evidence requires review.
- Password change, full profile/media export, Party Mode, lyrics, statistics, and production account management are not represented as working operations by the frontend.
- Publication still requires the recorded LGPL/MPL notice/relinking review and NVIDIA redistribution review.

Read the complete [RC1 release notes](docs/release/RELEASE_NOTES_RC1.md), [risk register](docs/implementation/RISK_REGISTER.md), and [backup/restore runbook](docs/operations/BACKUP_RESTORE.md) before treating this repository as more than a local candidate.

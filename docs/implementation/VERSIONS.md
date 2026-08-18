# AutPlay Versions and Reproducibility Baseline

**Recorded:** 2026-08-18

**State:** P14 local CPU RC evidence complete, including physical Samsung SM-A556E qualification; P12 A-030 real RTX/model evidence explicitly deferred by ADR-027 and no model is activated

## Executable P01-P14 pins

### P14 release inventory

| Artifact | Exact record | Validation |
| --- | --- | --- |
| Local CPU RC image | `autplay-server:rc1-local`; immutable local image ID in `P14_RELEASE_BUILD.json` | digest-pinned multi-stage build; no push/deploy |
| Android RC APK | unsigned and debug-development-signed SHA-256 in `P14_RELEASE_BUILD.json` | release/R8, zipalign, v2/v3 signature, API 26 plus physical SM-A556E install/restart; not production-signed |
| Python SBOMs | CycloneDX 1.5 root/server/GPU documents under `docs/release/sbom/` | 36/46/55 components; hashes in `P14_RELEASE_INVENTORY.json` |
| Vulnerability evidence | OSV via uv audit, 2026-08-17 | root/server/GPU frozen locks: zero reported vulnerabilities/adverse statuses |
| Android release dependencies | `docs/release/ANDROID_RELEASE_DEPENDENCIES.txt` | Gradle `releaseRuntimeClasspath`; SHA-256 in release inventory |
| Release status | `LOCAL_RC_PASS` | physical Samsung SM-A556E install/background/battery-policy/process-death/restart and API 26 emulator evidence pass; no publication/deployment/production signing |
| Windows Vault descriptor mode | `O_BINARY` on staging/create/read/write | newline-bearing physical CAS byte regression plus production-adapter P14 restore/reconciliation drill |

### Server and quality tooling

| Component | Exact pin | File of record | Validation |
| --- | --- | --- | --- |
| CPython | `3.14.7` | `.python-version`, `server/pyproject.toml` | exact runtime assertion; both canonical P03 gates pass 298 tests |
| uv / uv_build | `0.12.3` / `0.12.3` | `server/pyproject.toml`, `server/uv.lock` | required-version, frozen sync, and lock freshness pass; 47 locked package stanzas |
| FastAPI | `0.141.1` | `server/pyproject.toml`, `server/uv.lock` | app factory, health/error/auth API tests and live container pass |
| Pydantic / settings | `2.13.4` / `2.15.0` | same | explicit-precedence, bounds, and sanitized-validation tests pass |
| Uvicorn | `0.51.0` | same | bounded single-worker API starts and serves loopback liveness/readiness |
| Prometheus client | `0.25.0` | same | isolated registry and low-cardinality API metric-route tests pass |
| Argon2 CFFI | `25.1.0` | same | explicit Argon2id hashing/verification primitive; password login remains disabled by ADR-016 |
| PyJWT | `2.13.0` | same | strict short-lived, session-bound HS256 token and real-session API tests pass |
| httpx2 | `2.9.0` (development only) | same | in-process ASGI/API test client; absent from production dependency group |
| SQLAlchemy / Alembic | `2.0.52` / `1.19.1` | same | clean `0001`-`0015` lifecycle, exact `75/67/19/49` physical inventory, 75-table/841-column typed mapping, guarded Wave downgrade and zero pending autogenerate operations |
| Psycopg binary | `3.3.4` | same | real PostgreSQL migrations, transactions, deferred constraints and concurrency tests pass |
| pgvector Python | `0.5.0` | same | unbounded `VECTOR()` mapping, reflection and real round-trip pass; no runtime dependency added |
| rfc8785 | `0.1.4` | same | canonical JSON vectors, SHA-256, byte bounds and persistence-command validation pass |
| Ruff / mypy / pytest | `0.16.2` / `2.3.0` / `9.1.1` | same | canonical lint/format/strict-mypy gates and the complete disposable-PostgreSQL suite include P12 registry/fencing/A-B/ACL evidence |

All direct requirements are exact. `server/uv.lock` contains 47 package stanzas. The universal dependency-tree audit finds no torch, TensorFlow, JAX, CuPy, CUDA, NVIDIA, ONNX Runtime, Transformers, or scikit-learn runtime in the CPU graph.

### Isolated P12 GPU project

| Component | Exact pin | File of record | Validation |
| --- | --- | --- | --- |
| Python / uv / uv_build | `3.14.7` / `0.12.3` / `0.12.3` | `gpu/pyproject.toml`, `gpu/uv.lock`, `gpu/Dockerfile` | separate frozen lock/import/build boundary; never synced by CPU canonical gates |
| FFmpeg / ffprobe | `8.1.2`; image digest `sha256:33f770f812cbfc3de96c547157fc9faf8bd95a36481753439ffa761045167585` | `gpu/Dockerfile` | same verified binary SHA-256 pins as CPU media image; deterministic bounded PCM adapter tests pass |
| GPU quality tooling | Ruff `0.16.2`, mypy `2.3.0`, pytest `9.1.1` | `gpu/pyproject.toml`, `gpu/uv.lock` | 21 device-selection, artifact/Vault-path, ONNX shape/device/OOM, task-safe composition, pooling, preprocessing and settings tests pass; two symlink tests skip on this Windows host |
| ONNX Runtime GPU | `onnxruntime-gpu[cuda,cudnn]==1.26.0` | `gpu/pyproject.toml`, `gpu/uv.lock`, `gpu/Dockerfile` | isolated 56-package GPU graph; CUDA provider is asserted at image build; runtime allowlist requires registry revision `1.26.0`, ONNX/FP32 and binds the selected device index |
| CUDA/cuDNN Python runtime | NVRTC `12.9.86`; runtime `12.9.79`; cuFFT `11.4.1.4`; cuRAND `10.3.10.19`; nvJitLink `12.9.86`; cuDNN `9.24.0.43`; cuBLAS `12.9.2.10` | `gpu/uv.lock` | exact transitive pins resolved by the ONNX `cuda,cudnn` extras; private to the GPU image; CPU lock/import audit remains free of NVIDIA/CUDA/ONNX packages |
| Local GPU validation image | manifest-list `sha256:e3020d6b29c2695deb8ae3eb0decc6d5b78fd668afc69bde8bc520926411fa33`; 2,297,185,893 bytes | `gpu/Dockerfile`; local Docker store only | current server/GPU wheels, non-root user, read-only `--check-config`, FFmpeg hashes and `CUDAExecutionProvider` import pass; image was not pushed or deployed |
| Candidate model | LAION-CLAP music checkpoint, research only | ADR-025 | not downloaded, registered or activated; exact checkpoint license/hash/runtime and RTX 3060 quality/throughput gate remain mandatory |

The GPU image uses the same digest-pinned uv/Python base as the CPU image and requests devices only
in the opt-in Compose profile. ONNX Runtime is [MIT-licensed](https://github.com/microsoft/onnxruntime/blob/main/LICENSE);
its official [CUDA execution-provider compatibility record](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html)
and [installation guide](https://onnxruntime.ai/docs/install/) are the sources for the CUDA 12.x/
cuDNN 9 selection and packaged runtime libraries. RTX 3060 / GA106 / sm_86 /
12 GB remains the current benchmark target only. Device requirements are configurable capabilities
and a future compatible card requires no application-business-logic change. No model license or
weights are implied by the runtime package.

### Codex development harness tooling

The repository root has a separate development-only uv project. It is absent from the server Docker allowlist and does not alter `server/uv.lock` or the CPU dependency audit.

| Component | Exact pin | File of record | Validation |
| --- | --- | --- | --- |
| AutPlay harness | `0.1.0` | root `pyproject.toml`, `uv.lock` | frozen build/import, six-command CLI smoke, strict lint/type/unit gates |
| Project Stop-hook phase pipeline | schema `1` | `.codex/hooks.json`, `AUTPLAY_CODEX_PHASE_PIPELINE.json`, ignored local state | official hook shape; no new dependency; frozen CLI/manifest validation and transition simulations |
| OpenAI Codex Python SDK | `openai-codex==0.144.4` | root `pyproject.toml`, `uv.lock` | package metadata identifies OpenAI/openai-codex; workspace-write implementation and deny-all/read-only reviewer adapter tests |
| Bundled Codex CLI | `openai-codex-cli-bin==0.144.4` | root `uv.lock` | resolved transitively by the SDK; harness does not depend on the separately installed host CLI version |
| JSON Schema validator | `jsonschema==4.26.0` | root `pyproject.toml`, `uv.lock` | Draft 2020-12 schemas checked and valid/invalid examples executed |
| JSON Schema typing | `types-jsonschema==4.26.0.20260518` | root `pyproject.toml`, `uv.lock` | strict mypy covers schema validation tests |
| OpenAPI validator | `openapi-spec-validator==0.9.0` | root `pyproject.toml`, `uv.lock` | OpenAPI 3.1 source validates in the 51-test P04 contract gate |
| RFC 8785 canonicalizer | `rfc8785==0.1.4` | root `pyproject.toml`, `uv.lock` | Golden client-event hashes are recomputed byte-for-byte in the P04 gate |
| Ruff / mypy / pytest | `0.16.2` / `2.3.0` / `9.1.1` | root `pyproject.toml`, `uv.lock` | complete harness tree passes format, lint, strict type, and the complete current unit suite |

The root lock contains 37 package stanzas. Recorded wheel SHA-256 values for direct selected dependencies are `openai-codex` `de1513a6e94b9a8d7728a3b74298bc1469428ade10ba0ef2d5db47dd1cb606f5`, `jsonschema` `d489f15263b8d200f8387e64b4c3a75f06629559fb73deb8fdfb525f2dab50ce`, `types-jsonschema` `30b30a518c7fe335df85c919fcbcc631b69c03d4a4b5b632fa916bea03065307`, `openapi-spec-validator` `222fecffc7714f6d0a6ad62c0e4b66cc2b7dbfafb7b93acfc6c308abbdb51af8`, and `rfc8785` `520d690b448ecf0703691c76e1a34a24ddcd4fc5bc41d589cb7c58ec651bcd48`; the committed lock retains platform-specific artifact hashes for the complete graph.

### Android bootstrap

| Component | Exact pin | File of record | Validation |
| --- | --- | --- | --- |
| JDK | Microsoft OpenJDK `17.0.20+8-LTS` | ADR-013 / bootstrap gates | exact `java -version`; Gradle launcher assertion; daemon forced by CLI property |
| Gradle | `9.3.1` | wrapper properties | distribution SHA-256 verified; wrapper launch passes |
| Gradle distribution SHA-256 | `b266d5ff6b90eada6dc3b20cb090e3731302e553a27c5d3e4df1f0d76beaff06` | wrapper properties | downloaded archive matched |
| Wrapper JAR SHA-256 | `b3a875ddc1f044746e1b1a55f645584505f4a10438c1afea9f15e92a7c42ec13` | generated wrapper | matched official wrapper checksum |
| Android Gradle Plugin | `9.1.0` | `gradle/libs.versions.toml`; ADR-020 | Kotlin 2.4.10 published compatibility upper bound; host/device/full gates pass |
| Kotlin / Compose compiler | `2.4.10` / `2.4.10` | root build + version catalog | built-in Kotlin override; compile/unit/lint pass |
| Compose BOM | `2026.06.01` | version catalog | minimal UI compile/lint pass |
| Activity Compose / JUnit | `1.13.0` / `4.13.2` | version catalog | host unit test and APK build pass |
| Android SDK | compile `36.1`; target `36`; min `26`; Build Tools `36.1.0` | app build script | lint/unit/assemble pass |
| KSP2 | `2.3.9` | version catalog/root build | Room processors compile debug, release, host-test and Android-test sources |
| Room / SQLite | `3.0.1` / `2.7.0` | version catalog/app build | additive schema v10 export; named non-destructive migrations v1-v10; Wave recovery/preflight/queue projections add no credentials, paths, URLs or clock samples; API 26 v9-v10 preservation and release R8 pass |
| AndroidX Media3 | `1.10.1` | version catalog/app build; ADR-021 | ExoPlayer, MediaSessionService, DownloadService/Manager/Index and data-source routing pass host, API 26 and minified-release/R8 gates |
| Kotlin coroutines | `1.10.2` | version catalog | Room application transaction and Compose command flow compile/test |
| kotlinx.serialization JSON | `1.11.0` | version catalog/app build | bounded tree validation and additive safe-value preservation; malformed, depth, sensitive-key and exact UTF-8 byte-limit host tests pass |
| Java JSON Canonicalization | `1.1` | version catalog/app build; ADR-020 | RFC 8785 Java implementation; jar SHA-256 `ed12a01f28d147898312963a1f704e90290b67a61f34fa3a761f41c134f4e691`; official number vector and duplicate-key tests pass |
| DataStore / WorkManager | `1.2.1` / `2.11.2` | version catalog | non-secret profile/session settings and connected, exponential-backoff, stable-ID-only sync work pass API 26 evidence |
| OkHttp | `5.4.0` | version catalog/app build; ADR-022 | bounded authenticated sync transport, structured reset errors, typed ACK/pull/bootstrap decoding and query encoding pass host/API 26 gates |
| Android Core KTX | `1.18.0` | version catalog/app build | exact stable release compatible with `compileSdk 36.1`; `content://` validation uses the official `String.toUri` extension |
| AndroidX test runner / ext JUnit / core | `1.7.0` / `1.3.0` / `1.7.0` | version catalog | 82 connected instrumentation tests pass on API 26 |

AGP/Gradle/dependency “new version available” lint detectors are deliberately disabled; exact upgrades are an ADR/compatibility operation. All other Android lint warnings are errors. `android.overridePathCheck=true` permits the verified Windows workspace path containing Cyrillic; forcing Gradle `file.encoding=UTF-8` is intentionally avoided because it corrupts worker classpath arguments on Windows code page 1251.

P07 activated the pre-recorded AGP `9.1.0` fallback after `9.1.1` built-in Kotlin compiled host-test classes but omitted them from the AndroidUnitTest runtime classpath. Clean/source-layout attempts reproduced the failure; the exact one-patch rollback restored execution and retained Kotlin/Compose/Room/KSP, API 26, lint, debug and minified release/R8 evidence. ADR-020 records the decision and rejected broader toolchain changes. P08 retains that baseline and adds exact Media3 `1.10.1`; ADR-021 records its ownership/security/cache boundary. P09 adds exact OkHttp `5.4.0` without adding Retrofit/Hilt; ADR-022 records the sync boundary. P10 adds no dependency pin; ADR-023 records its import/identity/review boundary. P11 reuses exact OkHttp `5.4.0` for bounded pack transport and adds only its same-version MockWebServer test artifact; ADR-024 records the recommendation boundary. P13 adds no dependency pin; ADR-026 reuses OkHttp, Room and Media3 behind the Wave recovery/execution boundary.

Room schema export v1 remains normalized SHA-256 `f063c8ec14ecf8c1fbd7d926f5e9322021e1187c2bf6c486c6b9a6aed88924d2`; v2 is `c69acd49acceadf9c1c92874ab2eca9069c6958f1bd4c313136ed8a5e80d3acf`. P09 exports v3 `b071f4b8f302cbd9ee514ccf4f89845c23b68ce58784b203de6efef0812f0c3a`, v4 `6a6d94abaa98a27df9cc7dc442b4c31478e95e3e184c6160858d2b5c275fd8b5`, v5 `744237d953246c1a21725aa8156c229c3b9b208a98c3b43d4d415c1ceb793c4b`, v6 `921b2f5dc88affd4623f39bc873688aef7130d1d63bc23c760070310b3d26ea5`, and v7 `ff44bce40b9934784d9022e7eee8ada7ac86fee34624dd8e7be2ac91d93a0b9d`. P10 exports v8 `7639eb1f005957e057a76812ec4a1a7a2699ed5c451443b4883dda309d73f82c`. P11 exports v9 `f7764762cdc29efe25c285e53b0cce6c513dfba0e4a491dfc9ffd2bdcb915d62`. P13 v10 has Room identity hash `eff029c0b73e3189b9ab8e31b0261541` and exported-file SHA-256 `9f42becf68b2bd5a92a1bf788dbc3cda361894db3690d1fa9a77f6cd34aa7c90`; API 26 migration evidence preserves v9 queue/recommendation state while adding the three bounded Wave projections, with no destructive fallback.

### Disposable database

| Component | Exact pin | File of record | Runtime evidence |
| --- | --- | --- | --- |
| PostgreSQL | `18.4` (`Debian 18.4-1.pgdg12+1`) | image digest + runtime query | `SHOW server_version` |
| pgvector | `0.8.6` | image digest + extension query | `pg_extension.extversion` |
| OCI image | `pgvector/pgvector:0.8.6-pg18-bookworm@sha256:691673308c99d2161ba298736f3147f1f22d79de2fb7ec93ae9b4afcab870b62` | `deploy/compose/compose.yaml` | healthy service; exact config image; scoped cleanup |

The base image service publishes no host port. The test-only Compose override assigns a random `127.0.0.1` port; its project-scoped volume is disposable and was confirmed absent after every canonical run.

### P06 CPU runtime image and media tools

| Component | Exact pin | File of record | Runtime evidence |
| --- | --- | --- | --- |
| uv/Python OCI image | `ghcr.io/astral-sh/uv:0.12.3-python3.14-trixie-slim@sha256:93035a1ae478ef905cc75b107bfe1fde62cdebf5b1996206dd4e5089a9f0a6d3` | `server/Dockerfile` | Multi-stage frozen no-development install; API/worker/stream run as non-root UID 999 on read-only root filesystems |
| FFmpeg / ffprobe | `8.1.2`; source image `mwader/static-ffmpeg:8.1.2@sha256:33f770f812cbfc3de96c547157fc9faf8bd95a36481753439ffa761045167585` | `server/Dockerfile` | Binary SHA-256: ffmpeg `7b3fb9508c20166ab3ba236a9585c3e22e903880723c1a6448e69ae6e4cd88d2`; ffprobe `fe39eb91eb04dd18dff3870a87b59e41be997476c2d373c46ff7e12bb284743c`; build asserts versions and hashes |
| Chromaprint / fpcalc | `1.6.1` | `server/Dockerfile` | Official archive SHA-256 `fc16cd37a70168040bc9ceb45f1d4d1216f5a75bc4c9cf8564bea70ac6a45733`; fpcalc binary SHA-256 `e7b14fbf9d544f6ba99b7aced3c07786258e09e37cfcb054a41d2a6eeb0887a7` |

The P06 image is shared by migration, API, CPU-worker and isolated direct-stream services and contains no GPU stage. The stream process receives the Vault volume read-only and imports no media-tool adapter. The reproducible `scripts/test-p06-media-runtime.ps1` run used `--network none` and a read-only container, decoded and fingerprinted a generated 12-second FLAC (`duration_ms=12000`, 52 fingerprint bytes), rejected a truncated file and hostile JSON-like non-audio payload, and placed both in recoverable quarantine. The runtime Compose smoke completed migration `0011`, kept API/worker/stream healthy, ran bounded `vault-reconcile`, proved the stream mount read-only and removed every scoped resource.

## Official source record

P01/P02 sources were reviewed on 2026-08-12/13. P03 package records were added and runtime-validated on 2026-08-15; exact package metadata pages are the record, not an instruction to auto-upgrade.

- [Python 3.14.7 release](https://www.python.org/downloads/release/python-3147/)
- [uv 0.12.3 release](https://github.com/astral-sh/uv/releases/tag/0.12.3) and [uv build backend](https://docs.astral.sh/uv/configuration/build-backend/)
- Exact PyPI metadata: [FastAPI 0.141.1](https://pypi.org/project/fastapi/0.141.1/), [Pydantic 2.13.4](https://pypi.org/project/pydantic/2.13.4/), [pydantic-settings 2.15.0](https://pypi.org/project/pydantic-settings/2.15.0/), [Uvicorn 0.51.0](https://pypi.org/project/uvicorn/0.51.0/), [prometheus-client 0.25.0](https://pypi.org/project/prometheus-client/0.25.0/), [argon2-cffi 25.1.0](https://pypi.org/project/argon2-cffi/25.1.0/), [PyJWT 2.13.0](https://pypi.org/project/PyJWT/2.13.0/), [httpx2 2.9.0](https://pypi.org/project/httpx2/2.9.0/), [SQLAlchemy 2.0.52](https://pypi.org/project/SQLAlchemy/2.0.52/), [Alembic 1.19.1](https://pypi.org/project/alembic/1.19.1/), [Psycopg 3.3.4](https://pypi.org/project/psycopg/3.3.4/), [pgvector Python 0.5.0](https://pypi.org/project/pgvector/0.5.0/), [rfc8785 0.1.4](https://pypi.org/project/rfc8785/0.1.4/), [Ruff 0.16.2](https://pypi.org/project/ruff/0.16.2/), [mypy 2.3.0](https://pypi.org/project/mypy/2.3.0/), [pytest 9.1.1](https://pypi.org/project/pytest/9.1.1/)
- Root tooling/contract metadata: [openai-codex 0.144.4](https://pypi.org/project/openai-codex/0.144.4/), [jsonschema 4.26.0](https://pypi.org/project/jsonschema/4.26.0/), [types-jsonschema 4.26.0.20260518](https://pypi.org/project/types-jsonschema/4.26.0.20260518/), [openapi-spec-validator 0.9.0](https://pypi.org/project/openapi-spec-validator/0.9.0/), and [rfc8785 0.1.4](https://pypi.org/project/rfc8785/0.1.4/)
- [Microsoft OpenJDK downloads](https://learn.microsoft.com/en-us/java/openjdk/download) and [major-version URLs/checksums](https://learn.microsoft.com/en-us/java/openjdk/download-major-urls)
- [AGP 9.1.0 compatibility](https://developer.android.com/build/releases/agp-9-1-0-release-notes), [built-in Kotlin migration](https://developer.android.com/build/migrate-to-built-in-kotlin), [Kotlin Gradle compatibility](https://kotlinlang.org/docs/gradle-configure-project.html), and [Compose setup](https://developer.android.com/develop/ui/compose/setup-compose-dependencies-and-compiler)
- [RFC 8785](https://datatracker.ietf.org/doc/html/rfc8785) and its referenced [Java canonicalization implementation](https://github.com/erdtman/java-json-canonicalization); [Maven Central 1.1 record](https://central.sonatype.com/artifact/io.github.erdtman/java-json-canonicalization/1.1)
- [Room 3 releases](https://developer.android.com/jetpack/androidx/releases/room3), [KSP releases](https://github.com/google/ksp/releases), [kotlinx.serialization setup](https://kotlinlang.org/docs/serialization-get-started.html), [kotlinx.serialization releases](https://github.com/Kotlin/kotlinx.serialization/releases), [DataStore releases](https://developer.android.com/jetpack/androidx/releases/datastore), and [WorkManager releases](https://developer.android.com/jetpack/androidx/releases/work)
- [Media3 releases](https://developer.android.com/jetpack/androidx/releases/media3), [background playback](https://developer.android.com/media/media3/session/background-playback), and [downloading media](https://developer.android.com/media/media3/exoplayer/downloading-media)
- [Android command-line tools](https://developer.android.com/studio#command-tools) and [SDK packages](https://developer.android.com/tools/sdkmanager)
- [Gradle 9.3.1 release](https://docs.gradle.org/9.3.1/release-notes.html), [wrapper checksums](https://gradle.org/release-checksums/), and [wrapper verification](https://docs.gradle.org/current/userguide/wrapper.html)
- [PostgreSQL 18.4 release](https://www.postgresql.org/docs/release/18.4/), [pgvector v0.8.6](https://github.com/pgvector/pgvector/tree/v0.8.6), and [official image tag metadata](https://hub.docker.com/v2/repositories/pgvector/pgvector/tags/0.8.6-pg18-bookworm/)

## Observed validation environment

| Area | Observed evidence | Interpretation |
| --- | --- | --- |
| Host | Windows NT `10.0.26200`, x86_64; PowerShell 5.1 | Development evidence, not production OS |
| uv/Python | uv `0.12.3`; uv-managed CPython `3.14.7` | Exact P03 frozen baseline |
| Root tooling/contracts | root lock with 37 package stanzas; host CLI `0.147.0`; SDK/bundled CLI `0.144.4` | Frozen harness plus P04 JSON Schema/OpenAPI/RFC 8785 validators remain outside the CPU server graph |
| P04 contract gate | Windows PowerShell and Git Bash; Python `3.14.7` | Each canonical path passed 51 device-independent sync/interaction contract tests before the unchanged 298-test server/database suite |
| P02 shell gates | PowerShell 5.1 and Git Bash | Both canonical server-only paths passed all 225 tests |
| P03 jobs evidence | PowerShell 5.1; disposable PostgreSQL 18.4/pgvector 0.8.6 | Eight unit plus thirteen real-database P03 job tests pass inside both complete canonical gates; scoped resources cleaned |
| P03 shell gates | PowerShell 5.1 and Git Bash | Each passed lock/import/Ruff/format/strict-mypy/CPU audit and all 298 tests against PostgreSQL 18.4/pgvector 0.8.6; PowerShell `120.24s`, Git Bash `126.11s`; cleanup passed |
| P03 runtime profile | Docker Engine `29.6.1`, Compose `5.2.0`, Linux containers | Image built from the digest-pinned base and a `96.21 kB` allowlisted context; migration exited 0; API/worker UID 999 and read-only; liveness/readiness, exact-head worker health, `CPU_ONLY`, and graceful worker restart passed; zero scoped residue |
| Android host | Microsoft OpenJDK `17.0.20+8-LTS`; SDK platform `36.1`; Build Tools `36.1.0` | P11 lint, 45 host tests, debug APK and minified release/R8 APK pass; JDK archive SHA-256 `e46fd292317c6bb0a8fe9dc63115021329f3a63caeba791c185f89f3666a68e5` |
| Docker | Engine `29.6.1` Linux; Compose `5.2.0`; PostgreSQL `18.4`; pgvector `0.8.6` | Exact P02 runtime queries; every scoped container/network/volume removed |
| Device | disposable API 26 x86_64 AVD (`codex_p13_api26`) with bundled SQLite; physical Samsung SM-A556E (`arm64-v8a`, SDK 36) | 82 connected emulator tests pass, including Room v9-v10 Wave preservation, joined Android/server sync, recommendation impression scrolling, playback/download/sync/import/recovery and Home binding paths. Physical dev-signed install/background/battery-policy/process-death/restart passes without clearing user data. |
| SDK tools | command-line tools package `15859902`; archive SHA-256 `90ae805d20434428bffcb699c290860f19bb5f66a67e6b330067e3de801fb04a` | API 26 system image provisioned; older AGP SDK parser still emits a non-blocking XML-v4 warning while all declared gates pass |

Machine-specific install/cache paths are intentionally omitted from the project baseline.

## Researched but deferred first-use candidates

These are not installed, locked, or accepted runtime dependencies:

| Owning phase | Candidate direction | Required gate |
| --- | --- | --- |
| P12 | CUDA/runtime/model registry | Isolated optional profile, licenses, hashes and RTX 3060 benchmark |

## Upgrade rule

An exact pin changes only after official compatibility/security review, clean frozen/bootstrap checks, updated lock/checksum/digest evidence, and an ADR/version/handoff update. The words `current` or `latest` never constitute a pin.

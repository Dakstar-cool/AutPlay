# AutPlay Versions and Reproducibility Baseline

**Recorded:** 2026-08-12

**State:** P00 design baselines only; exact project pins are owned by P01 or first-use validation

## Design baselines (not yet executable pins)

| Component | Design baseline | P00 state |
| --- | --- | --- |
| Production server | Linux x86_64; CPU core cross-platform where practical | Architecture baseline; exact support matrix unresolved (P00-D009) |
| Server language/workflow | Python through `uv` with committed `uv.lock` | Accepted direction; Python and dependency versions unresolved |
| API | FastAPI | Accepted direction; exact version unresolved |
| Persistence | SQLAlchemy 2 typed + Alembic 1.x | Accepted direction; exact versions unresolved |
| Database | PostgreSQL 18.x | Major fixed; patch, image tag and immutable digest unresolved |
| Vector extension | pgvector 0.8-compatible range; exact search first | Exact patch/image digest unresolved; no ANN index before benchmark |
| PostgreSQL driver | Psycopg 3 preferred | Requires P01/P02 async/sync/migration evidence |
| Android | Kotlin + Jetpack Compose | Exact Kotlin/Compose/JDK/AGP/Gradle set unresolved |
| Local database | Room 3.0.1 preferred + BundledSQLiteDriver, KSP, FTS5, WAL | Requires compatibility gate; Room 2.8.4 fallback only before user schema v1 |
| Android SDK | Preliminary minSdk 26; target/compile current compatible stable at bootstrap | Exact compile/target SDK and device matrix unresolved |
| Playback/downloads | AndroidX Media3; DownloadIndex owns durable progress | Exact version unresolved |
| Deferred work | AndroidX WorkManager | Exact version unresolved |
| Android HTTP/JSON/DI | Retrofit + OkHttp, Kotlinx serialization, Hilt preferred | Choices and exact versions require P01 ADR/first real use |
| Vault | Local/NAS filesystem adapter | Filesystem/backup target intentionally deferred |
| Media tools | FFmpeg/ffprobe and Chromaprint/fpcalc | Exact packages/build hashes and codec build unresolved |
| GPU | Optional isolated NVIDIA RTX 3060 12 GB worker | CUDA/runtime/model set unresolved until P12 benchmark |
| Containers | Exact version tags for development; immutable digests for release | No project image selected in P00; `latest` forbidden |

## Observed P00 workstation tools (evidence, not project requirements)

| Tool | Observed value | Interpretation |
| --- | --- | --- |
| OS | Windows NT 10.0.26200.0 | Development host only; not production baseline |
| PowerShell | 5.1.26100.8875 | Used for P00 read-only checks |
| Git | 2.51.0.windows.1 | Repository operations available |
| Python | 3.12.10 | Installed locally; not approved/pinned for AutPlay |
| uv | 0.11.6 | Installed locally; not approved/pinned for AutPlay |
| Java | Java 8 runtime; `javac` not on PATH | Insufficient evidence for Android build baseline |
| Docker CLI | 29.6.1 | CLI available; project Compose/runtime health not validated |
| Node/npm/Gradle/Podman | Not found on PATH | Not a P00 blocker; P01 resolves required tooling |
| Android environment | `ANDROID_HOME` is set; `adb`, `sdkmanager`, and `avdmanager` not on PATH | SDK/device build evidence unavailable at P00 |

Machine-specific installation paths are intentionally not recorded.

## Exact resolutions required in P01

| Area | Required pin/evidence |
| --- | --- |
| Python | Exact supported Python version, `.python-version`, `requires-python`, uv version strategy and clean `uv sync --frozen` |
| Server dependencies | FastAPI, Pydantic/settings, SQLAlchemy, Alembic, Psycopg, Ruff, mypy, pytest and logging versions in `uv.lock` |
| Android toolchain | JDK, Gradle wrapper, AGP, Kotlin, KSP, Compose compiler/BOM, Android compile/target/min SDK and clean debug/release smoke |
| Android libraries | Room, Bundled SQLite, Media3, WorkManager and selected DI/HTTP/JSON versions with compatibility evidence |
| PostgreSQL | Exact PostgreSQL 18 patch image and digest; disposable health smoke |
| pgvector | Exact compatible 0.8 release/image digest and extension availability |
| CI | Hosting-aware CI runtime images/actions or platform-neutral local plan; immutable major pinning policy |

## Later first-use resolutions

- P02: Alembic/driver/database integration compatibility and schema head.
- P05: physical/minSdk Room 3, FTS5, R8 and process-restart gate.
- P06: FFmpeg/ffprobe and Chromaprint/fpcalc package/build versions and hashes.
- P12: GPU driver/runtime, model revision/license/SHA-256/preprocessing/dimension.
- P14: release container digests, SBOM, generated contracts/schemas and full version manifest.

## Files of record

When their owning phase creates them, reproducibility is recorded in:

- `.python-version`;
- `server/pyproject.toml` and `server/uv.lock`;
- `gradle/libs.versions.toml` and `gradle/wrapper/gradle-wrapper.properties`;
- `deploy/compose/*.yaml`;
- committed Room exported schemas;
- generated OpenAPI/event schemas;
- Alembic head and schema inventory;
- model registry rows/manifests with source, license, SHA-256 and runtime;
- this file and phase handoffs.

Do not treat a locally installed version or the word “current/latest” as a reproducibility pin. A pin becomes approved only with the owning clean build/test evidence.

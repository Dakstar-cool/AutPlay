# AutPlay Versions and Reproducibility Baseline

**Recorded:** 2026-08-15

**State:** P03 CPU server-runtime baseline verified

## Executable P01/P02/P03 pins

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
| SQLAlchemy / Alembic | `2.0.52` / `1.19.1` | same | 57 typed mappings; clean `0001`-`0010` upgrade/base/upgrade and zero pending autogenerate operations |
| Psycopg binary | `3.3.4` | same | real PostgreSQL migrations, transactions, deferred constraints and concurrency tests pass |
| pgvector Python | `0.5.0` | same | unbounded `VECTOR()` mapping, reflection and real round-trip pass; no runtime dependency added |
| rfc8785 | `0.1.4` | same | canonical JSON vectors, SHA-256, byte bounds and persistence-command validation pass |
| Ruff / mypy / pytest | `0.16.2` / `2.3.0` / `9.1.1` | same | whole-tree lint and 101-file format check pass; strict mypy passes 87 files; 298 tests pass in both shells |

All direct requirements are exact. `server/uv.lock` contains 47 package stanzas. The universal dependency-tree audit finds no torch, TensorFlow, JAX, CuPy, CUDA, NVIDIA, ONNX Runtime, Transformers, or scikit-learn runtime in the CPU graph.

### Android bootstrap

| Component | Exact pin | File of record | Validation |
| --- | --- | --- | --- |
| JDK | Microsoft OpenJDK `17.0.20+8-LTS` | ADR-013 / bootstrap gates | exact `java -version`; Gradle launcher assertion; daemon forced by CLI property |
| Gradle | `9.3.1` | wrapper properties | distribution SHA-256 verified; wrapper launch passes |
| Gradle distribution SHA-256 | `b266d5ff6b90eada6dc3b20cb090e3731302e553a27c5d3e4df1f0d76beaff06` | wrapper properties | downloaded archive matched |
| Wrapper JAR SHA-256 | `b3a875ddc1f044746e1b1a55f645584505f4a10438c1afea9f15e92a7c42ec13` | generated wrapper | matched official wrapper checksum |
| Android Gradle Plugin | `9.1.1` | `gradle/libs.versions.toml` | official matrix requires Gradle 9.3.1/JDK 17; full gate passes |
| Kotlin / Compose compiler | `2.4.10` / `2.4.10` | root build + version catalog | built-in Kotlin override; compile/unit/lint pass |
| Compose BOM | `2026.06.01` | version catalog | minimal UI compile/lint pass |
| Activity Compose / JUnit | `1.13.0` / `4.13.2` | version catalog | host unit test and APK build pass |
| Android SDK | compile `36.1`; target `36`; min `26`; Build Tools `36.1.0` | app build script | lint/unit/assemble pass |

AGP/Gradle/dependency “new version available” lint detectors are deliberately disabled; exact upgrades are an ADR/compatibility operation. All other Android lint warnings are errors. `android.overridePathCheck=true` permits the verified Windows workspace path containing Cyrillic; forcing Gradle `file.encoding=UTF-8` is intentionally avoided because it corrupts worker classpath arguments on Windows code page 1251.

The published Kotlin 2.4.10 table names AGP 9.1.0 as its fully supported upper bound, while the current stable patch is AGP 9.1.1. This is a one-patch documentation gap, not claimed matrix coverage: AGP officially permits a higher KGP through the root classpath override, and P01 mitigates the gap with actual Kotlin/Compose compilation, host tests, lint and APK evidence. Revert to 9.1.0 or revalidate before feature work if that executable gate regresses.

### Disposable database

| Component | Exact pin | File of record | Runtime evidence |
| --- | --- | --- | --- |
| PostgreSQL | `18.4` (`Debian 18.4-1.pgdg12+1`) | image digest + runtime query | `SHOW server_version` |
| pgvector | `0.8.6` | image digest + extension query | `pg_extension.extversion` |
| OCI image | `pgvector/pgvector:0.8.6-pg18-bookworm@sha256:691673308c99d2161ba298736f3147f1f22d79de2fb7ec93ae9b4afcab870b62` | `deploy/compose/compose.yaml` | healthy service; exact config image; scoped cleanup |

The base image service publishes no host port. The test-only Compose override assigns a random `127.0.0.1` port; its project-scoped volume is disposable and was confirmed absent after every canonical run.

### CPU runtime image

| Component | Exact pin | File of record | Runtime evidence |
| --- | --- | --- | --- |
| uv/Python OCI image | `ghcr.io/astral-sh/uv:0.12.3-python3.14-trixie-slim@sha256:93035a1ae478ef905cc75b107bfe1fde62cdebf5b1996206dd4e5089a9f0a6d3` | `server/Dockerfile` | Multi-stage frozen no-development install; API/worker run as UID 999; final local image ID `sha256:5b1476db5d1648688b66d222f7a736d3300e1e377a7f453681cf1472de465f6f` |

The P03 runtime image is shared by migration, API, and CPU-worker services and contains no GPU stage. The final clean Compose run completed migration, served API liveness/readiness at `127.0.0.1:4336`, kept both API and worker healthy as UID 999 on read-only root filesystems, returned `CPU_ONLY` from the in-container import audit, stopped the worker with exit 0 on `SIGTERM`, restarted it healthy, and left zero scoped containers/networks/volumes. The worker environment contained no authentication signing secret.

## Official source record

P01/P02 sources were reviewed on 2026-08-12/13. P03 package records were added and runtime-validated on 2026-08-15; exact package metadata pages are the record, not an instruction to auto-upgrade.

- [Python 3.14.7 release](https://www.python.org/downloads/release/python-3147/)
- [uv 0.12.3 release](https://github.com/astral-sh/uv/releases/tag/0.12.3) and [uv build backend](https://docs.astral.sh/uv/configuration/build-backend/)
- Exact PyPI metadata: [FastAPI 0.141.1](https://pypi.org/project/fastapi/0.141.1/), [Pydantic 2.13.4](https://pypi.org/project/pydantic/2.13.4/), [pydantic-settings 2.15.0](https://pypi.org/project/pydantic-settings/2.15.0/), [Uvicorn 0.51.0](https://pypi.org/project/uvicorn/0.51.0/), [prometheus-client 0.25.0](https://pypi.org/project/prometheus-client/0.25.0/), [argon2-cffi 25.1.0](https://pypi.org/project/argon2-cffi/25.1.0/), [PyJWT 2.13.0](https://pypi.org/project/PyJWT/2.13.0/), [httpx2 2.9.0](https://pypi.org/project/httpx2/2.9.0/), [SQLAlchemy 2.0.52](https://pypi.org/project/SQLAlchemy/2.0.52/), [Alembic 1.19.1](https://pypi.org/project/alembic/1.19.1/), [Psycopg 3.3.4](https://pypi.org/project/psycopg/3.3.4/), [pgvector Python 0.5.0](https://pypi.org/project/pgvector/0.5.0/), [rfc8785 0.1.4](https://pypi.org/project/rfc8785/0.1.4/), [Ruff 0.16.2](https://pypi.org/project/ruff/0.16.2/), [mypy 2.3.0](https://pypi.org/project/mypy/2.3.0/), [pytest 9.1.1](https://pypi.org/project/pytest/9.1.1/)
- [Microsoft OpenJDK downloads](https://learn.microsoft.com/en-us/java/openjdk/download) and [major-version URLs/checksums](https://learn.microsoft.com/en-us/java/openjdk/download-major-urls)
- [AGP 9.1.1 compatibility](https://developer.android.com/build/releases/agp-9-1-0-release-notes), [built-in Kotlin migration](https://developer.android.com/build/migrate-to-built-in-kotlin), [Kotlin Gradle compatibility](https://kotlinlang.org/docs/gradle-configure-project.html), and [Compose setup](https://developer.android.com/develop/ui/compose/setup-compose-dependencies-and-compiler)
- [Gradle 9.3.1 release](https://docs.gradle.org/9.3.1/release-notes.html), [wrapper checksums](https://gradle.org/release-checksums/), and [wrapper verification](https://docs.gradle.org/current/userguide/wrapper.html)
- [PostgreSQL 18.4 release](https://www.postgresql.org/docs/release/18.4/), [pgvector v0.8.6](https://github.com/pgvector/pgvector/tree/v0.8.6), and [official image tag metadata](https://hub.docker.com/v2/repositories/pgvector/pgvector/tags/0.8.6-pg18-bookworm/)

## Observed validation environment

| Area | Observed evidence | Interpretation |
| --- | --- | --- |
| Host | Windows NT `10.0.26200`, x86_64; PowerShell 5.1 | Development evidence, not production OS |
| uv/Python | uv `0.12.3`; uv-managed CPython `3.14.7` | Exact P03 frozen baseline |
| P02 shell gates | PowerShell 5.1 and Git Bash | Both canonical server-only paths passed all 225 tests |
| P03 jobs evidence | PowerShell 5.1; disposable PostgreSQL 18.4/pgvector 0.8.6 | Eight unit plus thirteen real-database P03 job tests pass inside both complete canonical gates; scoped resources cleaned |
| P03 shell gates | PowerShell 5.1 and Git Bash | Each passed lock/import/Ruff/format/strict-mypy/CPU audit and all 298 tests against PostgreSQL 18.4/pgvector 0.8.6; PowerShell `120.24s`, Git Bash `126.11s`; cleanup passed |
| P03 runtime profile | Docker Engine `29.6.1`, Compose `5.2.0`, Linux containers | Image built from the digest-pinned base and a `96.21 kB` allowlisted context; migration exited 0; API/worker UID 999 and read-only; liveness/readiness, exact-head worker health, `CPU_ONLY`, and graceful worker restart passed; zero scoped residue |
| Android | P01: Microsoft OpenJDK `17.0.20+8-LTS`; SDK platform `36.1`; Build Tools `36.1.0` | Historical P01 lint/unit/APK evidence; Android files were unchanged and the exact JDK was not provisioned for the P02 run |
| Docker | Engine `29.6.1` Linux; Compose `5.2.0`; PostgreSQL `18.4`; pgvector `0.8.6` | Exact P02 runtime queries; every scoped container/network/volume removed |
| Device | No AVD or connected device | `connectedCheck` NOT RUN; not a P01 acceptance requirement |
| SDK metadata | Local tools warn they understand XML through v3 while one installed package uses v4 | Non-blocking; CI/P05 should provision a coherent current SDK tool set |

Machine-specific install/cache paths are intentionally omitted from the project baseline.

## Researched but deferred first-use candidates

These are not installed, locked, or accepted runtime dependencies:

| Owning phase | Candidate direction | Required gate |
| --- | --- | --- |
| P05 | Room `3.0.1`, SQLite `2.7.0`, KSP2 `2.3.9`, minSdk 26 | Room/KSP/FTS5/R8/fresh-open-restart/device compatibility before schema v1 |
| P05/P08 | WorkManager `2.11.2`, Media3 `1.10.1` | First real ownership/process-death tests |
| P04/P05 | Hilt; Retrofit + OkHttp; kotlinx.serialization | Exact pins with first real DI graph/typed contract consumer |
| P06 | FFmpeg/ffprobe and Chromaprint/fpcalc | Package/build hashes, codecs, crash/timeout evidence |
| P12 | CUDA/runtime/model registry | Isolated optional profile, licenses, hashes and RTX 3060 benchmark |

## Upgrade rule

An exact pin changes only after official compatibility/security review, clean frozen/bootstrap checks, updated lock/checksum/digest evidence, and an ADR/version/handoff update. The words `current` or `latest` never constitute a pin.

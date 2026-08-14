# AutPlay Versions and Reproducibility Baseline

**Recorded:** 2026-08-15

**State:** P02 executable PostgreSQL persistence baseline; later-phase libraries remain deferred until first validated use

## Executable P01/P02 pins

### Server and quality tooling

| Component | Exact pin | File of record | Validation |
| --- | --- | --- | --- |
| CPython | `3.14.7` | `.python-version`, `server/pyproject.toml` | explicit sync selector plus runtime assertion; import and 225 tests pass |
| uv / uv_build | `0.12.3` / `0.12.3` | `server/pyproject.toml`, `server/uv.lock` | required-version gate; frozen sync and lock check pass |
| FastAPI | `0.141.1` | `server/pyproject.toml`, `server/uv.lock` | import smoke passes |
| Pydantic / settings | `2.13.4` / `2.15.0` | same | import compatibility passes |
| SQLAlchemy / Alembic | `2.0.52` / `1.19.1` | same | 57 typed mappings; clean `0001`-`0010` upgrade/base/upgrade and zero pending autogenerate operations |
| Psycopg binary | `3.3.4` | same | real PostgreSQL migrations, transactions, deferred constraints and concurrency tests pass |
| pgvector Python | `0.5.0` | same | unbounded `VECTOR()` mapping, reflection and real round-trip pass; no runtime dependency added |
| rfc8785 | `0.1.4` | same | canonical JSON vectors, SHA-256, byte bounds and persistence-command validation pass |
| Ruff / mypy / pytest | `0.16.2` / `2.3.0` / `9.1.1` | same | lint/format, strict type check, 225 tests pass |

All direct requirements are exact. `server/uv.lock` resolves 35 packages on CPython 3.14 and contains no torch, TensorFlow, JAX, CuPy, CUDA or NVIDIA runtime package.

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

## Official source record

Sources were reviewed on 2026-08-12/13; exact package metadata pages are the compatibility record, not an instruction to auto-upgrade.

- [Python 3.14.7 release](https://www.python.org/downloads/release/python-3147/)
- [uv 0.12.3 release](https://github.com/astral-sh/uv/releases/tag/0.12.3) and [uv build backend](https://docs.astral.sh/uv/configuration/build-backend/)
- Exact PyPI metadata: [FastAPI 0.141.1](https://pypi.org/project/fastapi/0.141.1/), [Pydantic 2.13.4](https://pypi.org/project/pydantic/2.13.4/), [pydantic-settings 2.15.0](https://pypi.org/project/pydantic-settings/2.15.0/), [SQLAlchemy 2.0.52](https://pypi.org/project/SQLAlchemy/2.0.52/), [Alembic 1.19.1](https://pypi.org/project/alembic/1.19.1/), [Psycopg 3.3.4](https://pypi.org/project/psycopg/3.3.4/), [pgvector Python 0.5.0](https://pypi.org/project/pgvector/0.5.0/), [rfc8785 0.1.4](https://pypi.org/project/rfc8785/0.1.4/), [Ruff 0.16.2](https://pypi.org/project/ruff/0.16.2/), [mypy 2.3.0](https://pypi.org/project/mypy/2.3.0/), [pytest 9.1.1](https://pypi.org/project/pytest/9.1.1/)
- [Microsoft OpenJDK downloads](https://learn.microsoft.com/en-us/java/openjdk/download) and [major-version URLs/checksums](https://learn.microsoft.com/en-us/java/openjdk/download-major-urls)
- [AGP 9.1.1 compatibility](https://developer.android.com/build/releases/agp-9-1-0-release-notes), [built-in Kotlin migration](https://developer.android.com/build/migrate-to-built-in-kotlin), [Kotlin Gradle compatibility](https://kotlinlang.org/docs/gradle-configure-project.html), and [Compose setup](https://developer.android.com/develop/ui/compose/setup-compose-dependencies-and-compiler)
- [Gradle 9.3.1 release](https://docs.gradle.org/9.3.1/release-notes.html), [wrapper checksums](https://gradle.org/release-checksums/), and [wrapper verification](https://docs.gradle.org/current/userguide/wrapper.html)
- [PostgreSQL 18.4 release](https://www.postgresql.org/docs/release/18.4/), [pgvector v0.8.6](https://github.com/pgvector/pgvector/tree/v0.8.6), and [official image tag metadata](https://hub.docker.com/v2/repositories/pgvector/pgvector/tags/0.8.6-pg18-bookworm/)

## Observed validation environment

| Area | Observed evidence | Interpretation |
| --- | --- | --- |
| Host | Windows NT `10.0.26200`, x86_64; PowerShell 5.1 | Development evidence, not production OS |
| uv/Python | uv `0.12.3`; uv-managed CPython `3.14.7` | Exact P02 server baseline |
| P02 shell gates | PowerShell 5.1 and Git Bash | Both canonical server-only paths passed all 225 tests |
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

# ADR-013: P01 reproducible toolchain baseline

**Status:** Accepted

**Date:** 2026-08-13

**Owners:** AutPlay repository maintainers

## Context

P01 must turn the design-only repository into a reproducible CPU-first server and Android bootstrap without implementing P02+ behavior. Floating versions, locally observed defaults, or unverified container tags would make the first build non-repeatable.

## Decision drivers

- Exact, reviewable and committed pins.
- A supported Python 3.14 and Android JDK/Gradle/AGP/Kotlin combination.
- One CPU dependency graph with no CUDA/ML packages.
- PostgreSQL 18 plus pgvector in one disposable local service.
- Windows, Linux and macOS server bootstrap; Android build on a provisioned SDK host.

## Options considered

| Option | Reliability | Complexity | Cost | Risks |
| --- | --- | --- | --- | --- |
| Exact versions, lockfile, wrapper checksums and image digest | High | Medium | Low | Requires deliberate upgrades |
| Version ranges and mutable image tags | Low | Low | Low | Resolver and registry drift |
| Commit tool binaries/distributions | Medium | High | Repository bloat | Supply-chain provenance and platform coupling |

## Decision

- Pin CPython `3.14.7`, uv/uv_build `0.12.3`, and every direct Python dependency exactly in `server/pyproject.toml`; commit `server/uv.lock`.
- Pin Microsoft OpenJDK `17.0.20+8-LTS`, Gradle `9.3.1`, AGP `9.1.1`, Kotlin/Compose compiler `2.4.10`, Compose BOM `2026.06.01`, compileSdk `36.1`, targetSdk `36`, minSdk `26`, and Build Tools `36.1.0`.
- Commit the Gradle wrapper with distribution SHA-256 `b266d5ff6b90eada6dc3b20cb090e3731302e553a27c5d3e4df1f0d76beaff06`; verify the wrapper JAR against the official checksum.
- Use `pgvector/pgvector:0.8.6-pg18-bookworm@sha256:691673308c99d2161ba298736f3147f1f22d79de2fb7ec93ae9b4afcab870b62` for P01 local smoke only.
- Keep caches and generated artifacts untracked. Root scripts select and assert CPython `3.14.7`, reject a different uv/JDK/SDK/Gradle baseline, force Gradle's daemon to the validated `JAVA_HOME`, and reject reuse of the fixed disposable Compose project.
- Deliberately suppress only Android lint's version-availability detectors. Versions change through a compatibility review, not an unrelated lint run; all correctness warnings remain errors.

Exact dependency pins are committed in the Python locks, Gradle version catalog, wrapper
properties, and digest-pinned Compose files.

## Consequences

### Positive

- Clean bootstrap has one dependency resolution result and verifiable downloads.
- CPU-only server and disposable database assumptions are executable.
- Cross-platform server checks do not require Android or Docker.

### Negative

- Patch upgrades require an ADR/version record update and full P01-equivalent smoke.
- Android SDK/JDK prerequisites must be provisioned explicitly on CI hosts.
- `android.overridePathCheck=true` is required because the current Windows workspace contains non-ASCII path components.

## Compatibility and migration

This ADR creates no data migration or API compatibility promise. P02 validates the selected database driver/migration runtime against real schema work. P05 owns Room/KSP/Media3/WorkManager activation and device compatibility; their researched candidates are not P01 dependencies.

## Validation evidence

- Frozen Python sync, import, Ruff, mypy and 13 pytest cases pass on CPython 3.14.7.
- Android lint, host unit test and debug APK build pass on Microsoft JDK 17.0.20 and Gradle 9.3.1.
- Runtime SQL reports PostgreSQL `18.4` and vector extension `0.8.6`; scoped resources are absent after cleanup.
- The root bootstrap/check scripts reproduce these checks from committed configuration.

## Reversal trigger

Revisit when an owning phase requires a capability unavailable in this matrix, an official compatibility/security change invalidates a pin, or clean Windows/Linux/macOS evidence fails. Never perform a broad toolchain upgrade inside an unrelated feature phase.

# P02 Platform-Neutral CI Plan

**Status:** Ready to encode after a hosting provider is selected

**Canonical commands:** root [`README.md`](../../README.md)

No Git remote or CI host is configured. P02 therefore defines provider-neutral jobs and required evidence without adding provider-specific YAML or mutable marketplace actions.

## Required jobs

| Job | Host / prerequisites | Canonical command | Required evidence |
| --- | --- | --- | --- |
| Server Windows | Windows x64; uv `0.12.3`; Docker Engine/Compose | `scripts/check.ps1 -ServerOnly` through the README invocation | Frozen sync, import, Ruff, format, strict mypy, CPU graph, 225 tests against PostgreSQL 18.4/pgvector 0.8.6, cleanup |
| Server Linux | Linux x86_64; uv `0.12.3`; Docker Engine/Compose | `bash scripts/check.sh --server-only` | Same server and real-database evidence from an empty workspace cache |
| Server macOS | Supported macOS; uv `0.12.3`; Docker Engine/Compose | `bash scripts/check.sh --server-only` | Same server and real-database evidence; no Android requirement |
| Android | Linux x86_64; Microsoft JDK `17.0.20+8-LTS`; Android platform `36.1`, Build Tools `36.1.0`; Docker | `bash scripts/check.sh` | Wrapper checksum/resolution, lint, host unit test, debug APK, plus the complete P02 database suite and cleanup |

WSL is treated as Linux only when its own Linux JDK and Android SDK are provisioned. A Windows SDK path mounted into WSL is not accepted as portable CI evidence.

## Execution policy

1. Start from a fresh checkout with no `.venv`, Gradle cache, Docker project resources, build outputs, or generated local config.
2. Provision uv, Microsoft JDK and Android SDK from approved official sources; verify exact versions before the scripts run.
3. Use the committed `uv.lock`, version catalog, wrapper and image digest. Do not rewrite locks in CI.
4. Preserve machine-readable test/lint reports and the debug APK as short-lived CI artifacts; do not commit them.
5. Let each script use its PID-scoped `autplay-p02-*` project name and random loopback database port. The script refuses pre-existing scoped resources and verifies cleanup, so independent jobs need no shared fixed project.
6. Mark connected/device tests `NOT RUN` in P01. Add an emulator/device matrix only in the owning Android phase; do not turn absent devices into a fake pass.

## Cache policy

- Caches are optional accelerators, never evidence. A scheduled or pre-merge cold-cache run is required.
- Key uv caches by OS, architecture, Python/uv pin and `server/uv.lock` hash.
- Key Gradle caches by OS, architecture, JDK, wrapper properties and version-catalog hashes.
- Never cache `.env`, credentials, signing material, PostgreSQL volumes, APK signing keys, or project-local user data.

## Merge gate

P02-equivalent changes require all four jobs green after CI exists. Persistence changes additionally require one Alembic head, clean metadata comparison, exact `57/53/13/40` inventory, upgrade/base/upgrade lifecycle evidence, and the full real-database invariant suite. Toolchain/lock/digest changes also require official-source review, a cold run, updated `VERSIONS.md`, and an ADR/handoff evidence update.

# P01 Platform-Neutral CI Plan

**Status:** Ready to encode after a hosting provider is selected

**Canonical commands:** root [`README.md`](../../README.md)

No Git remote or CI host is configured. P01 therefore defines provider-neutral jobs and required evidence without adding provider-specific YAML or mutable marketplace actions.

## Required jobs

| Job | Host / prerequisites | Canonical command | Required evidence |
| --- | --- | --- | --- |
| Server Windows | Windows x64; uv `0.12.3` | `scripts/check.ps1 -ServerOnly` through the README invocation | Frozen sync, import, Ruff, format, mypy, pytest, CPU graph |
| Server Linux | Linux x86_64; uv `0.12.3` | `bash scripts/check.sh --server-only` | Same server checks from an empty workspace cache |
| Server macOS | Supported macOS; uv `0.12.3` | `bash scripts/check.sh --server-only` | Same server checks; no Docker/Android requirement |
| Android | Linux x86_64; Microsoft JDK `17.0.20+8-LTS`; Android platform `36.1`, Build Tools `36.1.0`; Docker | `bash scripts/check.sh` | Wrapper checksum/resolution, lint, host unit test, debug APK, PostgreSQL/pgvector runtime and cleanup |

WSL is treated as Linux only when its own Linux JDK and Android SDK are provisioned. A Windows SDK path mounted into WSL is not accepted as portable CI evidence.

## Execution policy

1. Start from a fresh checkout with no `.venv`, Gradle cache, Docker project resources, build outputs, or generated local config.
2. Provision uv, Microsoft JDK and Android SDK from approved official sources; verify exact versions before the scripts run.
3. Use the committed `uv.lock`, version catalog, wrapper and image digest. Do not rewrite locks in CI.
4. Preserve machine-readable test/lint reports and the debug APK as short-lived CI artifacts; do not commit them.
5. Run the Android/database job serially for the fixed `autplay-p01-smoke` project name. The script refuses pre-existing scoped resources and verifies cleanup.
6. Mark connected/device tests `NOT RUN` in P01. Add an emulator/device matrix only in the owning Android phase; do not turn absent devices into a fake pass.

## Cache policy

- Caches are optional accelerators, never evidence. A scheduled or pre-merge cold-cache run is required.
- Key uv caches by OS, architecture, Python/uv pin and `server/uv.lock` hash.
- Key Gradle caches by OS, architecture, JDK, wrapper properties and version-catalog hashes.
- Never cache `.env`, credentials, signing material, PostgreSQL volumes, APK signing keys, or project-local user data.

## Merge gate

P01-equivalent changes require all four jobs green after CI exists. Toolchain/lock/digest changes additionally require official-source review, a cold run, updated `VERSIONS.md`, and an ADR/handoff evidence update.

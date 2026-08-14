# P03 Platform-Neutral CI Plan

**Status:** P03 local Windows/Git-Bash/container gates verified; ready to encode after a hosting provider is selected

**Canonical commands:** root [`README.md`](../../README.md)

No Git remote or CI host is configured. P03 therefore defines provider-neutral jobs and required evidence without adding provider-specific YAML, mutable marketplace actions, or real credentials.

## Required jobs

| Job | Host / prerequisites | Canonical command | Required evidence |
| --- | --- | --- | --- |
| Server Windows | Windows x64; uv `0.12.3`; Docker Engine/Compose | `scripts/check.ps1 -ServerOnly` through the README invocation | Frozen sync/lock, import, Ruff, format, strict mypy, CPU graph, complete current pytest suite against PostgreSQL 18.4/pgvector 0.8.6, exact cleanup |
| Server Linux | Linux x86_64; uv `0.12.3`; Docker Engine/Compose | `bash scripts/check.sh --server-only` | Same clean server/config/API/auth/job and real-database evidence from an empty workspace cache |
| Server macOS | Supported macOS; uv `0.12.3`; Docker Engine/Compose | `bash scripts/check.sh --server-only` | Same server and real-database evidence; no Android requirement |
| CPU runtime smoke | Linux x86_64; Docker Engine/Compose; ephemeral development signing-secret file | Runtime-profile commands in the root README | Image builds from the digest-pinned base; migration completes; API and worker start without CUDA; liveness/readiness pass; no public bind; graceful stop and scoped cleanup |
| Android | Linux x86_64; Microsoft JDK `17.0.20+8-LTS`; Android platform `36.1`, Build Tools `36.1.0`; Docker | `bash scripts/check.sh` | Wrapper checksum/resolution, lint, host unit test, debug APK, plus the complete current server/database suite and cleanup |

WSL is treated as Linux only when its own Linux JDK and Android SDK are provisioned. A Windows SDK path mounted into WSL is not accepted as portable CI evidence.

## P03 evidence requirements

The server jobs must fail if any of these regress:

- settings precedence, bounded validation, missing-secret behavior, or secret-safe representations;
- API liveness independence from PostgreSQL, readiness failure semantics, Alembic-head check, stable error/request IDs, redacted JSON logging, or low-cardinality metrics;
- local-only owner bootstrap, Argon2id parameters, refresh rotation/replay/revoke, active-principal reload, or owner-scoped authorization;
- atomic disjoint job claim, `attempt_no` fencing, expired-lease recovery, heartbeat/checkpoint, bounded deterministic retry, owner cancellation, or safe terminal transitions;
- API/CPU-worker import or startup if a torch, TensorFlow, JAX, CuPy, CUDA, NVIDIA, or other accelerator runtime enters the core graph;
- appearance of a placeholder feature endpoint or default feature job handler.

Password login must remain a negative startup/configuration case until an approved credential-persistence contract and migration exist. CI must never work around this gate by storing a credential in an unrelated column.

## Execution policy

1. Start from a fresh checkout with no `.venv`, Gradle cache, Docker project resources, build outputs, generated local config, or secret file.
2. Provision uv, Microsoft JDK and Android SDK from approved official sources; verify exact versions before the scripts run.
3. Use the committed `uv.lock`, version catalog, wrapper and image digest. Do not rewrite locks in CI.
4. Generate the runtime signing-secret file only inside the job's restricted temporary directory, mount it through the documented Compose secret, redact its path/value, and remove it during job cleanup.
5. Preserve machine-readable test/lint reports and the debug APK as short-lived CI artifacts; never preserve tokens, configuration secrets, database volumes, or owner/session payloads.
6. Let each script use a PID-scoped Compose project and random loopback database port. The script must refuse pre-existing scoped resources and verify cleanup, so independent jobs need no shared fixed project.
7. Keep the runtime smoke loopback-only and use disposable synthetic data. Never point a CI manifest at a personal or production database.
8. Mark connected/device tests `NOT RUN` in P01. Add an emulator/device matrix only in the owning Android phase; do not turn absent devices into a fake pass.

## Cache policy

- Caches are optional accelerators, never evidence. A scheduled or pre-merge cold-cache run is required.
- Key uv caches by OS, architecture, Python/uv pin and `server/uv.lock` hash.
- Key Gradle caches by OS, architecture, JDK, wrapper properties and version-catalog hashes.
- Never cache `.env`, credentials, signing material, PostgreSQL volumes, APK signing keys, tokens, or project-local user data.

## Merge gate

P03-equivalent changes require all five jobs green after CI exists. The server gate must include the complete current suite, not a frozen historical test count. Persistence inventory remains exactly `57/53/13/40` with one Alembic head at `0010_indexes_privileges`; P03 introduces no migration. Toolchain/lock/digest changes additionally require official-source review, a cold run, an updated `VERSIONS.md`, and ADR/handoff evidence. A-002 becomes `PASS` only after clean locked API and CPU-worker start evidence plus the structural/import CUDA audit are recorded in `HANDOFF_P03.md`.

# AutPlay

AutPlay is an Android-first, local-first music platform with an optional personal server. P01 established the reproducible monorepo foundation. P02 adds the executable PostgreSQL schema v1 through Alembic, complete typed SQLAlchemy persistence mappings, and real-database invariant tests. No HTTP endpoint, sync engine, matcher/product behavior, media feature, or production deployment is present yet.

## Pinned prerequisites

- `uv 0.12.3`; it installs the pinned CPython `3.14.7` runtime.
- Microsoft OpenJDK `17.0.20+8-LTS` with `JAVA_HOME` set.
- Android SDK platform `36.1`, Build Tools `36.1.0`, and `ANDROID_HOME` set.
- Docker Engine with a Docker Compose release that supports `up --wait` (Compose `5.2.0` is the recorded P01 host observation).

The committed Gradle wrapper downloads Gradle `9.3.1` and verifies the distribution checksum. A device or emulator is not required for the P01 lint, host-unit-test, and APK build smoke. WSL needs its own Linux JDK and Android SDK; a Windows SDK path is not a portable WSL substitute.

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

`bootstrap` installs the pinned Python with uv, performs a frozen dependency sync, resolves the Gradle wrapper, and validates Compose configuration. `check` repeats the frozen bootstrap, runs Python import/Ruff/format/mypy checks, audits the CPU dependency graph, optionally runs Android lint/unit/assemble smoke, then owns a uniquely named disposable PostgreSQL project. It publishes PostgreSQL only on a random loopback port, verifies PostgreSQL/pgvector versions, runs the complete migration/invariant suite, and removes the test container, network, and volume. `ServerOnly`/`--server-only` skips Android but still requires Docker because P02 persistence tests use the real database.

See [`docs/implementation/CI_PLAN.md`](docs/implementation/CI_PLAN.md) for the hosting-neutral job matrix and [`docs/implementation/HANDOFF_P02.md`](docs/implementation/HANDOFF_P02.md) for P02 evidence.

## Repository boundaries

| Path | Current content |
| --- | --- |
| `apps/android` | Minimal standalone Compose application and host unit test |
| `server/src/autplay` | Framework-independent boundaries, canonical identity-evidence validation, and typed PostgreSQL adapter mappings |
| `server/migrations` | Linear Alembic revisions `0001` through `0010` for PostgreSQL schema v1 |
| `contracts/openapi`, `contracts/events` | P04 ownership placeholders only |
| `deploy/compose` | One digest-pinned PostgreSQL 18 + pgvector service and loopback-only test override |
| `tests/e2e`, `tests/fixtures` | Future owning-phase placeholders |
| `docs/adr` | Accepted toolchain, dependency-boundary, and immutable identity-history decisions |

Development Compose data is disposable and must never contain real/user data. Production roles/deployment, secrets, signing, public networking, API behavior, and external providers are outside P02.

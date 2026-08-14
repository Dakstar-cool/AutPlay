# AutPlay

AutPlay is an Android-first, local-first music platform with an optional personal server. P01 established the reproducible monorepo foundation and P02 made PostgreSQL schema v1 executable through Alembic with complete typed persistence mappings. P03 adds a production-shaped, CPU-only FastAPI/API process, PostgreSQL lease worker, typed runtime configuration, health/metrics/logging boundaries, and real owner/device session endpoints. Sync, matcher, Vault/media, and other non-auth product behavior remain future-phase work.

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

`bootstrap` installs the pinned Python with uv, performs a frozen dependency sync, resolves the Gradle wrapper, and validates Compose configuration. `check` repeats the frozen bootstrap, runs Python import/Ruff/format/mypy checks, audits the CPU dependency graph, optionally runs Android lint/unit/assemble smoke, then owns a uniquely named disposable PostgreSQL project. It publishes PostgreSQL only on a random loopback port, verifies PostgreSQL/pgvector versions, runs the complete server suite including P03 runtime/auth/job evidence, and removes the test container, network, and volume. `ServerOnly`/`--server-only` skips Android but still requires Docker because persistence and runtime repository tests use the real database.

See [`docs/implementation/CI_PLAN.md`](docs/implementation/CI_PLAN.md) for the hosting-neutral job matrix and [`docs/implementation/HANDOFF_P03.md`](docs/implementation/HANDOFF_P03.md) for the P03 evidence state.

## Disposable P03 runtime

The runtime overlay starts migration, API, and CPU-worker processes on the same CPU image built from a digest-pinned base. Supply a local development signing-secret file containing at least 32 random characters through `AUTPLAY_RUNTIME_AUTH_SECRET_FILE`; keep that file outside the repository. The API is published only on loopback.

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

These commands are for disposable local development only. See [`deploy/compose/README.md`](deploy/compose/README.md) for boundaries and probes.

## Repository boundaries

| Path | Current content |
| --- | --- |
| `apps/android` | Minimal standalone Compose application and host unit test |
| `server/src/autplay` | Domain/application/port boundaries; typed PostgreSQL persistence; CPU-only API, auth, observability, and worker runtime |
| `server/migrations` | Linear Alembic revisions `0001` through `0010` for PostgreSQL schema v1 |
| `contracts/openapi`, `contracts/events` | P04 ownership placeholders only |
| `deploy/compose` | Digest-pinned PostgreSQL 18 + pgvector base, loopback-only test override, and disposable CPU runtime profile |
| `tests/e2e`, `tests/fixtures` | Future owning-phase placeholders |
| `docs/adr` | Accepted toolchain, dependency-boundary, and immutable identity-history decisions |

Development Compose data is disposable and must never contain real/user data. P03 does not provide production TLS/domain/role topology, public registration, password credential persistence, sync or feature endpoints, external providers, or production deployment approval.

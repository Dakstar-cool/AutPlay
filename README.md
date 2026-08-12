# AutPlay

AutPlay is an Android-first, local-first music platform with an optional personal server. P01 establishes only a reproducible monorepo foundation: an empty typed server package, a minimal standalone Compose shell, contract and test placeholders, and one disposable PostgreSQL service. No schema, endpoint, sync contract, media feature, or product domain logic exists yet.

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

`bootstrap` installs the pinned Python with uv, performs a frozen dependency sync, resolves the Gradle wrapper, and validates Compose configuration. `check` repeats the frozen bootstrap, then runs Python import/Ruff/format/mypy/pytest checks, audits the CPU dependency graph, runs Android lint/unit/assemble smoke, starts the exact PostgreSQL/pgvector image until healthy, verifies runtime versions, and removes the project-scoped test container/network/volume. `ServerOnly`/`--server-only` is for the cross-platform CPU server matrix.

See [`docs/implementation/CI_PLAN.md`](docs/implementation/CI_PLAN.md) for the hosting-neutral job matrix and [`docs/implementation/HANDOFF_P01.md`](docs/implementation/HANDOFF_P01.md) for phase evidence once P01 closes.

## Repository boundaries

| Path | P01 content |
| --- | --- |
| `apps/android` | Minimal standalone Compose application and host unit test |
| `server/src/autplay` | Empty domain/application/ports/adapters/entrypoints package boundaries |
| `server/migrations` | P02 ownership placeholder only |
| `contracts/openapi`, `contracts/events` | P04 ownership placeholders only |
| `deploy/compose` | One disposable PostgreSQL 18 + pgvector development service |
| `tests/e2e`, `tests/fixtures` | Future owning-phase placeholders |
| `docs/adr` | Accepted P01 toolchain and dependency-boundary decisions |

Development Compose data is disposable and must never contain real/user data. Production deployment, secrets, signing, public networking, and external providers are outside P01.

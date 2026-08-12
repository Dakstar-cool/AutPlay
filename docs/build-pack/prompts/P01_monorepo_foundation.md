# P01 - Monorepo Foundation and Reproducible Toolchains

Выполни только phase P01. Следуй common protocol и прочитай `HANDOFF_P00.md`.

## Цель

Создать минимальный воспроизводимый monorepo skeleton, который собирает пустой Android client и импортирует пустой Python server package, поднимает disposable PostgreSQL, запускает smoke checks и готов к P02 без speculative feature code.

## Inputs

- `docs/design/AutPlay System Architecture v1.md`
- `docs/design/AutPlay_Design_Package_v1.md`
- `docs/build-pack/VERSION_POLICY.md`
- P00 plan/versions/risks

## Scope

1. Создай approved repository paths:
   - `apps/android`;
   - `server/src/autplay/{domain,application,ports,adapters,entrypoints}`;
   - `server/tests`;
   - `server/migrations` placeholder config path only;
   - `contracts/{openapi,events}`;
   - `deploy/compose`;
   - `docs/adr`;
   - `tests/e2e` and `tests/fixtures`.
2. Resolve and pin compatible stable Python/JDK/Kotlin/AGP/Gradle baseline using official sources.
3. Configure Python through `uv`, typed package, Ruff, mypy and pytest.
4. Configure a minimal Android Compose application, version catalog and Gradle wrapper.
5. Create local Compose profile with PostgreSQL 18 + pgvector only, healthcheck and disposable development volume.
6. Add root README with canonical bootstrap/check commands for Linux and Windows/WSL where supported.
7. Add CI smoke workflow if hosting platform is known; otherwise create a platform-neutral CI plan and local commands.
8. Record ADRs for exact toolchain and Android DI/network choices. Dependency can be deferred until first real use.

## Constraints

- No Redis, RabbitMQ, NATS, Kafka, MinIO or vector service.
- No production secrets or floating `latest` tags.
- No domain entities beyond compile smoke placeholder.
- No API endpoints except optional non-network package smoke.
- No Room schema or Alembic schema yet.
- Android application must not require server to launch.

## Acceptance

- `uv sync --frozen` works from clean state after lock creation.
- Ruff, mypy and pytest smoke are green.
- Gradle configuration resolves and Android unit/build smoke is green when SDK is available.
- PostgreSQL/pgvector disposable service becomes healthy and is removable without touching user data.
- CPU environment has no CUDA/ML dependency.
- Canonical commands documented once and referenced by AGENTS.md.
- VERSIONS.md contains exact pins and evidence.

## Required checks

Run all available server and Android smoke commands plus Compose config validation. If Android SDK/device is unavailable, do not claim PASS: add CI task/evidence path and mark exact environment blocker.

Create `HANDOFF_P01.md`, update matrices, make one local commit if authorized, and stop.

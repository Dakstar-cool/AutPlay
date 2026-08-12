# HANDOFF P01 - Monorepo Foundation

## Outcome

P01 is complete and green. The repository now has a minimal reproducible monorepo foundation: a typed empty CPU server package, a standalone Compose Android shell, exact lock/wrapper/catalog pins, contract/test/migration placeholders, one disposable PostgreSQL 18 + pgvector service, canonical bootstrap/check scripts, two accepted P01 ADRs and a provider-neutral CI plan. No P02 persistence or product feature work was started.

## Delivered scope

- Added root repository/build contract files, exact Python pin, Gradle wrapper/catalog, and one-command Windows/POSIX bootstrap/check entrypoints.
- Added `server/src/autplay` package boundaries for domain/application/ports/adapters/entrypoints, a `py.typed` marker, exact direct dependency pins, committed `uv.lock`, and compatibility/CPU smoke tests.
- Added a minimal standalone Android Compose app, launcher resources and one host unit test. It launches without a server/network/database dependency.
- Added placeholders only for future migrations, OpenAPI/events, end-to-end tests and fixtures.
- Added one Compose service pinned by OCI digest; verified healthy PostgreSQL 18.4 and pgvector 0.8.6, then removed its container/network/volume.
- Accepted ADR-013 for the exact P01 toolchain and ADR-014 for Android DI/network/JSON deferral and Media3 boundary.
- Added a platform-neutral Windows/Linux/macOS CPU and Linux Android/Compose CI plan because no Git remote/hosting provider is configured.
- Updated progress, traceability, risk, versions and A-001 evidence while preserving all future acceptance rows.
- Performed independent read-only audits of the P01 scope, server graph and Android matrix; corrected every high-confidence P01 issue found.

## Explicitly not delivered

- No Alembic configuration/revision, SQLAlchemy table mapping, executable reference DDL, PostgreSQL schema, seed data or migration lifecycle. P02 owns these.
- No API endpoint, worker, health route, domain entity, sync envelope, OpenAPI/event payload or real contract. P03/P04 own these.
- No Room, KSP, Media3, WorkManager, Hilt, Retrofit, OkHttp, Android sync, playback or download behavior. P05+ own these.
- No Redis, broker, S3/MinIO, separate vector database, GPU/CUDA/ML dependency, production Compose/deployment, secret, signing or external provider.
- No connected Android/device test: no AVD or device was available, and P01 requires lint/host-unit/APK evidence rather than instrumentation.
- No P02 work and no resolution of P00-D003. That schema/spec decision requires explicit user approval before P02.

## Changed modules/files

Root and scripts:

- `.gitattributes`, `.python-version`, `.gitignore`, `README.md`;
- `settings.gradle.kts`, `build.gradle.kts`, `gradle.properties`, `gradlew`, `gradlew.bat`, `gradle/libs.versions.toml`, `gradle/wrapper/*`;
- `scripts/bootstrap.ps1`, `scripts/bootstrap.sh`, `scripts/check.ps1`, `scripts/check.sh`;
- updated `AGENTS.md` to reference the canonical root commands.

Server/client/placeholders:

- `server/pyproject.toml`, `server/uv.lock`, `server/README.md`, `server/src/autplay/**`, `server/tests/test_package_smoke.py`, `server/migrations/README.md`;
- `apps/android/build.gradle.kts`, manifest, minimal Kotlin/Compose source, resources and host test;
- `contracts/openapi/README.md`, `contracts/events/README.md`, `tests/e2e/README.md`, `tests/fixtures/README.md`;
- `deploy/compose/compose.yaml`, `deploy/compose/README.md`.

Documentation/evidence:

- `docs/adr/ADR-013-p01-reproducible-toolchain.md`;
- `docs/adr/ADR-014-android-bootstrap-dependency-boundaries.md`;
- `docs/implementation/CI_PLAN.md` and this handoff;
- updated `PLAN.md`, `PROGRESS.md`, `TRACEABILITY.md`, `RISK_REGISTER.md`, `VERSIONS.md`, and A-001 in `MVP_ACCEPTANCE_MATRIX.md`.

## Decisions and ADRs

1. ADR-013 pins CPython 3.14.7, uv/uv_build 0.12.3, exact Python dependencies, Microsoft OpenJDK 17.0.20+8-LTS, Gradle 9.3.1, AGP 9.1.1, Kotlin/Compose compiler 2.4.10, Compose BOM 2026.06.01, Android SDK 36.1/target 36/min 26/Build Tools 36.1.0, PostgreSQL 18.4 and pgvector 0.8.6 via one image digest.
2. ADR-014 uses manual wiring in P01 and defers Hilt/Retrofit/OkHttp/kotlinx.serialization exact pins until a real owning-phase consumer. Media3 remains the owner of playback/download byte delivery.
3. The published Kotlin 2.4.10 matrix names AGP 9.1.0 as its upper fully-supported version while the current stable patch is 9.1.1. This one-patch documentation gap is explicit in `VERSIONS.md`; actual Kotlin/Compose compile, lint, unit and APK gates pass.
4. Android lint treats correctness warnings as errors but suppresses only update-availability detectors; exact versions change through compatibility review.
5. Windows Gradle daemon encoding is not forced to UTF-8: with this Cyrillic workspace it corrupts worker classpath arguments. Native code page plus `android.overridePathCheck=true` is the validated local setting.

## Migrations and contracts

None. `server/migrations`, `contracts/openapi`, and `contracts/events` contain ownership placeholders only. There is no `alembic.ini`, Alembic environment/revision, OpenAPI document or event schema.

## Commands executed

The root [`README.md`](../../README.md) is the single canonical command source. Key phase-exit executions:

| Command / validator | Result | Evidence |
| --- | --- | --- |
| `uv lock --project server --check` and `uv sync --project server --frozen` | PASS | Fresh lock; 33 packages; CPython 3.14.7 |
| Canonical `scripts/check.ps1 -ServerOnly` via README invocation | PASS | Import, Ruff lint/format, strict mypy, 13 pytest cases, structural universal JSON dependency audit |
| `gradlew.bat --no-daemon --console=plain lintDebug testDebugUnitTest assembleDebug` | PASS | 51-task graph; lint, host unit and debug APK green on exact JDK/Gradle/SDK baseline |
| Canonical full `scripts/check.ps1` via README invocation | PASS | Server + Android + database lifecycle completed in 80 seconds on warm cache |
| Runtime SQL in full check | PASS | `18.4 (Debian 18.4-1.pgdg12+1)|0.8.6` |
| Compose project resource audit after full check | PASS | 0 labeled containers, 0 volumes, 0 networks |
| Gradle archive and wrapper-JAR SHA-256 comparison | PASS | Distribution `b266d5...aff06`; wrapper JAR `b3a875...2ec13` |
| Clean staged-index export running canonical full check | PASS | Fresh export without `.venv`, build outputs or project resources; exact result recorded before commit |
| Markdown link/fence and phase-number validation | PASS | Final counts recorded in phase-close audit; 0 broken/unbalanced/missing/duplicate |
| `git diff --cached --check` and staged executable-mode audit | PASS | No whitespace errors; `gradlew` mode `100755` |

## Acceptance criteria

| Criterion | Status | Evidence |
| --- | --- | --- |
| Minimal monorepo paths and typed boundaries exist | PASS | Root inventory and changed-file list above |
| Python environment is frozen and imports cleanly | PASS | Exact pyproject/lock; canonical server-only/full checks |
| Ruff, strict mypy and pytest pass | PASS | Ruff green; mypy reports 7 source files; 13/13 tests pass |
| CPU server graph has no CUDA/GPU/ML package | PASS | Universal uv JSON resolution is structurally scanned; current 33-package set clean |
| Gradle wrapper/version catalog and standalone app build | PASS | Exact checksum/pins; lint/unit/assemble green |
| Android has no synchronous server dependency | PASS | Minimal static source/dependency review; no network/DB/framework dependency |
| PostgreSQL 18 + pgvector service is healthy and disposable | PASS | Runtime 18.4/0.8.6 and zero scoped resources after cleanup |
| Root README is canonical for Windows/Linux/macOS/WSL | PASS | README commands invoke checked scripts; CI plan references rather than duplicates sequence |
| DI/network/JSON choices and exact toolchain ADR exist | PASS | ADR-013 and ADR-014 |
| Hosting-neutral CI smoke plan exists | PASS | `CI_PLAN.md`; no unsupported provider-specific workflow added |
| A-001 has executable evidence | PASS | Traceability/matrix plus clean staged-index export full check |
| P02 and future features were not started | PASS | Scope/dependency inventory; placeholders only |
| Handoff exists and next prerequisite is exact | PASS | This file and blocker below |

## Known risks and debt

- P00-D003 / R-014 is a hard P02 prerequisite: current reference physical DDL cannot retain every immutable identity decision/evidence/history state/version required by the narrower Track Identity specification.
- The AGP 9.1.1/Kotlin 2.4.10 one-patch documentation gap is accepted only with the recorded executable gate; revalidate or use the documented 9.1.0 bound if it regresses.
- Connected/device/R8/Room/KSP/Media3/WorkManager compatibility remains P05 evidence. R-012 stays open.
- The local SDK emits a non-blocking XML v4 metadata warning from an older command-line parser; CI/P05 should provision a coherent current SDK tools set.
- Hosted Windows/Linux/macOS CI is planned but not executed because no hosting provider/remote exists. R-016 remains open pending cross-platform evidence.
- Cloud-synced filesystem behavior remains a P06 concern for Vault atomicity; P01 build/cache behavior succeeded in the current non-ASCII synced path.

## Preconditions for next phase

P02 must not start yet.

1. Obtain explicit user approval to resolve P00-D003 with a coordinated schema/spec change set: immutable general identity decision/evidence/history records; full decision-state vocabulary; query/normalization/extractor/matcher/calibrator/threshold versions; top-2/margin/origin/actor; supersession; and matching physical DDL/tests.
2. Record that approved resolution as an ADR/change set without silently weakening Track Identity requirements or changing frozen F-016 semantics.
3. Confirm this P01 phase commit is `HEAD`, the worktree is clean and this handoff remains green.
4. Only then execute `docs/build-pack/prompts/P02_postgresql_persistence.md`; do not combine the decision-resolution task and P02 implementation without explicit authorization.

Exact next request needed from the user:

```text
Разрешаю подготовить и утвердить change set для P00-D003: синхронизировать Track Identity, PostgreSQL schema/DDL и тестовые требования так, чтобы физическая модель неизменно сохраняла полную историю identity decisions/evidence и все требуемые состояния/версии/происхождение/supersession. Не начинать P02, пока change set не будет представлен и явно утверждён.
```

After that approval/change-set gate is accepted, the next phase request is:

```text
Выполни только AutPlay phase P02 по `docs/build-pack/prompts/P02_postgresql_persistence.md`. Следуй `docs/build-pack/PROMPT_PROTOCOL.md`, проверь `HANDOFF_P01.md` и утверждённый change set P00-D003. Не начинай P03. Подтверди acceptance P02 проверками, создай `docs/implementation/HANDOFF_P02.md` и остановись.
```

## Git state

- Branch: `master`.
- Parent P00 commit: `adf2bc8225f1747d3589428530e55dd794b6d85c`.
- Commit: P01 phase commit at `HEAD`; retrieve with `git rev-parse HEAD`. The exact self-hash cannot be embedded in its own contents and is reported in the completion response.
- Worktree: expected clean after the single local P01 commit; verified and reported immediately after commit.
- Push/PR: not performed; no remote configured.

## Blocking user decisions

P00-D003 change-set approval is required before P02. P00-D004 remains a later user-approved decision before deterministic identity reuse/matcher behavior in P06/P10; P01 did not reinterpret it.

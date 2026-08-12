# Version and Reproducibility Policy

Пакет собран 2026-08-12. Toolchain versions могут измениться до момента implementation, поэтому prompt pack отделяет frozen architecture от volatile dependency resolution.

## 1. Resolution rule

В P00/P01 Codex:

1. проверяет official release documentation;
2. выбирает совместимый stable set;
3. фиксирует versions в repository;
4. создает build smoke test;
5. записывает decision/evidence;
6. больше не обновляет versions между phases без необходимости.

## 2. Baselines

| Component | Baseline from design | Resolution |
| --- | --- | --- |
| PostgreSQL | 18.x | Latest validated patch in 18 series |
| pgvector | 0.8 compatible range | Pin exact validated release/image digest |
| Room | 3.0.1 baseline | Keep 3.0.1 or validated compatible patch after spike |
| Python | Not hard-coded in DDL | Select supported stable version, pin `.python-version` and `requires-python` |
| SQLAlchemy | 2.x typed | Pin exact minor/patch in `uv.lock` |
| Alembic | 1.x | Pin exact compatible version |
| Android | Kotlin/Compose/Media3/WorkManager | Version catalog + Gradle wrapper |
| FFmpeg | Required | Pin package/container version and record codec build |
| Chromaprint | Required for fingerprint path | Pin binary/library version and algorithm label |

## 3. Files of record

- `server/pyproject.toml`
- `server/uv.lock`
- `.python-version`
- `gradle/libs.versions.toml`
- `gradle/wrapper/gradle-wrapper.properties`
- `deploy/compose/*.yaml`
- `docs/implementation/VERSIONS.md`
- model registry records with source/license/SHA-256/runtime

## 4. Container rule

- Development Compose may use exact version tags.
- Release/production manifest records immutable digest.
- `latest`, floating major tag and unpinned downloaded binary are forbidden.
- GPU and CPU profiles use compatible but separate dependency sets.

## 5. Upgrade rule

Dependency upgrade is a dedicated change with:

- release-note/security review;
- lockfile diff;
- migration/schema compatibility check;
- affected tests;
- rollback path;
- ADR for major/toolchain changes.

Do not mix broad dependency upgrades into a feature phase.

## 6. Offline reproducibility evidence

Release candidate records:

- exact commit;
- lockfiles;
- container digests;
- generated OpenAPI/event schemas;
- Room exported schemas;
- Alembic head;
- model hashes;
- FFmpeg/Chromaprint version output;
- build/test commands and environment summary.

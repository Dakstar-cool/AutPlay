# Промт цели для Codex: AutPlay Schema Foundation v1

Работай непосредственно с файлами repository. Не печатай целиком измененные файлы в ответе.

## Цель

Реализуй первый bounded infrastructure slice AutPlay: воспроизводимую server persistence foundation на основе утвержденного design package. Результат должен поднимать чистую PostgreSQL database через Alembic и проверять ключевые invariants автоматическими tests. Не реализуй product features, HTTP API, Vault filesystem, Android UI, sync protocol или ML.

## Источники истины

Сначала полностью прочитай:

1. `ТЗ AutPlay.md`
2. `AutPlay System Architecture v1.md`
3. `AutPlay ER Model v1.md`
4. `AutPlay_Track_Identity_v1.md`
5. `AutPlay_PostgreSQL_Schema_v1.md`
6. `AutPlay_PostgreSQL_Schema_v1.sql`
7. repository-level `AGENTS.md`, если он существует

При конфликте не угадывай. Зафиксируй точные места и предложи минимальный ADR/change before implementation.

## Scope

1. Создай или дополни `server/` layout по System Architecture.
2. Настрой Python project через `uv` и committed lock file.
3. Добавь SQLAlchemy 2.x typed metadata/mappings для persistence schema без domain behavior.
4. Добавь Alembic configuration и initial revision, эквивалентную `AutPlay_PostgreSQL_Schema_v1.sql`.
5. Сохрани schemas, tables, named constraints, indexes, functions, triggers и pgvector extension behavior.
6. Добавь test fixture для disposable PostgreSQL 18 с pgvector.
7. Добавь migration/invariant tests.
8. Добавь короткий developer README с exact commands.

## Обязательные invariants tests

- clean `upgrade head` создает полный object inventory;
- migration может откатиться на empty development database;
- duplicate Vault SHA-256 отклоняется;
- hash length constraints работают;
- active user Track ref/library uniqueness работает вместе с tombstones;
- duplicate playlist Track разрешен, duplicate active position запрещен;
- device/user ownership mismatch отклоняется;
- duplicate device event sequence и idempotency key отклоняются;
- canonical audio variant принадлежит той же Recording;
- Recording redirect cycle отклоняется;
- job dependency cycle отклоняется;
- job claim поддерживает lease/heartbeat semantics;
- embedding dimension должна совпадать с Model Registry;
- destructive delete, нарушающий protected references, отклоняется.

## Ограничения

- PostgreSQL 18.x; pgvector version pin должен соответствовать design compatibility range.
- Python, uv, SQLAlchemy 2.x, Alembic 1.x и test dependencies фиксируются версиями.
- Server core обязан работать без CUDA/GPU dependencies.
- Не добавляй Redis, RabbitMQ, NATS, vector database или microservices.
- Не заменяй named CHECK на PostgreSQL enum.
- Не создавай HNSW/IVFFlat index в initial migration.
- Не ослабляй FK/trigger invariants ради упрощения ORM.
- Не используй floating container tag `latest`.
- Не меняй design documents молча.
- Не коммить secrets, tokens или machine-specific paths.
- Все новые code identifiers, scripts и comments - на английском.

## Verification commands

Подбери exact repository commands и добейся green результата минимум для:

```text
uv sync --frozen
uv run ruff check .
uv run mypy server/src
uv run pytest
alembic upgrade head
alembic downgrade base
alembic upgrade head
```

Если database lifecycle обернут task runner/container command, README должен содержать единственную каноническую команду и ее прямой эквивалент.

## Definition of done

- Fresh checkout воспроизводимо поднимает disposable PostgreSQL и schema head.
- Alembic head и reference DDL имеют одинаковый meaningful object inventory.
- Все обязательные migration/invariant tests проходят.
- CPU-only test run не импортирует GPU runtime.
- Не осталось placeholder migrations, skipped critical tests или destructive fallback.
- Финальный ответ кратко перечисляет измененные файлы, executed checks и реальные blockers. Не вставляй полные файлы.

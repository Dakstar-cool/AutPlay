# P02 - PostgreSQL Persistence Foundation

Выполни только phase P02. Следуй common protocol и прочитай `HANDOFF_P01.md`.

## Цель

Реализовать executable PostgreSQL schema v1 через Alembic и SQLAlchemy 2 typed persistence mappings, сохранив reference DDL invariants.

## Inputs

- `docs/design/AutPlay_PostgreSQL_Schema_v1.sql`
- `docs/design/AutPlay_PostgreSQL_Schema_v1.md`
- `docs/design/AutPlay ER Model v1.md`
- `docs/design/AutPlay_Track_Identity_v1.md`
- `docs/design/AutPlay System Architecture v1.md`

## Scope

1. Configure Alembic with deterministic naming and explicit initial revision.
2. Implement schema equivalent to reference DDL: schemas, extensions, 52 tables, 48 indexes, functions, triggers and grants/roles appropriate for tests.
3. Add typed SQLAlchemy persistence mappings without domain behavior.
4. Keep application/domain imports independent from SQLAlchemy.
5. Add disposable PostgreSQL integration fixture.
6. Add object inventory comparison between migrated DB and reference expectations.
7. Implement invariant tests listed in PostgreSQL schema decision document.
8. Document migration creation/review/rollback workflow.

## Mandatory tests

- clean upgrade to head;
- downgrade to base on empty development DB;
- upgrade again;
- SHA-256 length and Vault uniqueness;
- active UserTrackRef/Library uniqueness with tombstones;
- duplicate playlist Track allowed, duplicate active position rejected;
- cross-user/device ownership rejected;
- event sequence/idempotency uniqueness;
- canonical AudioVariant belongs to same Recording;
- Recording redirect and job dependency cycles rejected;
- embedding dimension/model/Recording integrity;
- named constraints and expected object counts.

## Constraints

- PostgreSQL text+named CHECK, not mutable database enums.
- No HNSW/IVFFlat in initial migration.
- No Vault bytes in DB.
- Do not weaken triggers/FK to simplify ORM.
- No HTTP API, sync engine or product feature logic.
- Autogenerate output must be reviewed; reference DDL remains physical contract.

## Acceptance

All migration/invariant tests pass against real pinned PostgreSQL 18 + pgvector. Alembic head, SQLAlchemy metadata and reference inventory have no unexplained drift.

Create `HANDOFF_P02.md`, update A-003/A-004 evidence and stop.

# AutPlay PostgreSQL Schema v1 - Decisions, Migration Plan and Test Matrix

**Статус:** Draft for executable migration implementation  
**Версия:** 1.0  
**Reference DDL:** [AutPlay_PostgreSQL_Schema_v1.sql](<AutPlay_PostgreSQL_Schema_v1.sql>)  
**Основание:** `AutPlay ER Model v1`, `AutPlay System Architecture v1`, `AutPlay Track Identity v1`  

---

# 1. Результат проектирования

Физическая серверная схема v1 фиксирует:

- PostgreSQL 18.x;
- pgvector 0.8.6 или более новый совместимый patch release серии 0.8;
- встроенный `uuidv7()` как default для server-created IDs;
- `bytea` длиной 32 bytes для SHA-256;
- `text + named CHECK` вместо PostgreSQL enum для изменяемых business states;
- прямые FK между module schemas внутри одной database;
- named constraints и indexes;
- partial unique indexes для active/tombstoned rows;
- PostgreSQL durable job queue;
- exact pgvector baseline без initial HNSW;
- database triggers только для критичных cross-table invariants;
- application transactions для domain commands и event emission.

Reference DDL содержит 52 таблицы, 48 indexes, 10 helper/constraint functions и 32 triggers.

---

# 2. Целевые версии

| Компонент | Решение v1 | Причина |
| --- | --- | --- |
| PostgreSQL | 18.x stable | Встроенный `uuidv7()`, актуальная стабильная major version |
| pgvector | 0.8.6+ в пределах validated compatibility range | Исправления HNSW/IVFFlat и поддержка PostgreSQL 18 |
| SQLAlchemy | 2.x typed declarative | `Mapped`/`mapped_column`, явное persistence mapping |
| Alembic | 1.x, pinned через `uv.lock` | Управляемые versioned migrations |
| Python | Фиксируется при создании repository | Не должен зашиваться в DDL |

Production image pinning выполняется по immutable digest. Автоматическое обновление major/minor database image запрещено.

Основания:

- [PostgreSQL 18 release](https://www.postgresql.org/about/news/postgresql-18-released-3142/)
- [PostgreSQL UUID functions](https://www.postgresql.org/docs/18/functions-uuid.html)
- [pgvector README](https://github.com/pgvector/pgvector/blob/master/README.md)
- [pgvector changelog](https://github.com/pgvector/pgvector/blob/master/CHANGELOG.md)
- [SQLAlchemy typed declarative tables](https://docs.sqlalchemy.org/en/20/orm/declarative_tables.html)
- [Alembic constraint naming](https://alembic.sqlalchemy.org/en/latest/naming.html)

---

# 3. Физические решения

## 3.1. UUID

Server-created PK:

```sql
uuid PRIMARY KEY DEFAULT uuidv7()
```

Client-created IDs для Offline Journal принимаются без генерации server-side. UUID:

- не выводится из metadata;
- не меняется после sync;
- не переиспользуется после merge;
- не раскрывает secret;
- проверяется как opaque identifier в API.

## 3.2. SHA-256

Хранение:

```sql
sha256 bytea NOT NULL CHECK (octet_length(sha256) = 32)
```

API использует lowercase hex. Persistence adapter преобразует hex <-> bytes на границе.

## 3.3. Статусы

Business status хранится как `text` с named CHECK.

Плюсы для AutPlay:

- проще expand/contract migration;
- Alembic видит named constraints;
- неизвестное API value можно обработать независимо от database enum lifecycle;
- нет скрытых `ALTER TYPE ... ADD VALUE` в транзакционно сложных migrations.

Lookup tables применяются только если status получает собственные metadata или управляется пользователем.

## 3.4. Время

- server time: `timestamptz`;
- UTC на API/worker boundary;
- `now()` внутри transaction используется для согласованной метки;
- `clock_timestamp()` используется только generic row-version trigger;
- client `occurred_at` не заменяет server `received_at`.

## 3.5. JSONB

JSONB используется для versioned payload/evidence/checkpoint, но не заменяет ключевые FK и query columns.

Application limits:

| Payload | Максимум v1 |
| --- | ---: |
| Sync event payload | 256 KiB |
| Job payload/checkpoint | 256 KiB каждый |
| Match feature evidence | 128 KiB |
| Source observation raw metadata | 1 MiB |
| Audit metadata | 64 KiB |

Крупные файлы и import inputs хранятся вне JSONB, по bounded reference.

## 3.6. Вектор

Initial schema использует unbounded-dimension `vector` column и cross-table trigger, проверяющий `vector_dims()` против Model Registry.

Причина: embedding dimension пока не выбрана benchmark на RTX 3060 12 GB.

Initial queries:

1. exact cosine distance;
2. filter по active `embedding_model_id`;
3. bounded candidate count;
4. измерение p95/p99 и recall.

HNSW создается отдельной migration после выбора модели. Index должен быть dimension-specific и model-specific. Старый и новый indexes сосуществуют на rollback window.

---

# 4. Database schemas и ownership

| Schema | Write owner module |
| --- | --- |
| `account` | Identity/Auth |
| `catalog` | Music Catalog |
| `identity` | Identity Resolution |
| `library` | User Library/History |
| `playlist` | Playlist Engine |
| `vault` | Vault/Ingest |
| `importing` | Library Migration |
| `sync` | Sync Engine |
| `jobs` | Job Scheduler |
| `ml` | Recommendation/ML |
| `audit` | Operations/Audit |
| `app_private` | Private DB functions only |

Module boundaries являются code-level rule. Общий SQLAlchemy session не дает модулю права произвольно менять чужие tables.

---

# 5. Database-enforced invariants

## 5.1. Enforced directly

- one `VaultObject` per SHA-256;
- SHA/request/model hashes имеют 32 bytes;
- one `AudioVariant` per v1 `VaultObject`;
- canonical variant принадлежит той же Recording;
- external reference имеет не более одного canonical target;
- active `UserTrackRef` unique per user/canonical Recording;
- active library row unique;
- duplicate playlist Track разрешен, active position key unique;
- device and user ownership pairs проверяются composite FK;
- listening/recommendation/taste objects не пересекают user boundary;
- event/device sequence и server event ID unique;
- idempotency scope/key unique;
- job dependency и recording redirect не должны образовывать cycle;
- embedding dimension совпадает с Model Registry;
- embedding source variant принадлежит той же Recording.

## 5.2. Enforced by command transaction plus tests

| Invariant | Почему не только CHECK/FK |
| --- | --- |
| Unresolved UserTrackRef имеет raw metadata или external reference | Association может добавляться в той же transaction после parent row |
| UserTrackRef coalesce при catalog merge | Требует переноса playlist/history и conflict policy |
| Split atomicity | Несколько entity families и audit change set |
| AudioVariant serving | Требует соединения variant + VaultObject + authorization |
| Vault GC reference check | Зависит от retention, replicas, jobs и grace period |
| Job graph cycle under concurrent writers | Trigger дополняется transaction advisory lock/application serialization |
| Recording redirect under concurrent merge | Требуется lock ordered by UUID и повторная проверка target |
| Embedding active alias switch | Требует index readiness и rollback policy |

Cross-table trigger не заменяет application authorization.

---

# 6. Database roles

Roles создаются deployment migration, а не portable reference DDL.

| Role | Права |
| --- | --- |
| `autplay_migrator` | DDL owner, используется только migration job |
| `autplay_api` | Ограниченный DML через application paths |
| `autplay_worker_cpu` | Jobs, ingest, import, Vault metadata |
| `autplay_worker_gpu` | Read catalog/Vault metadata, write `ml` results and own jobs |
| `autplay_stream` | Read-only authorization projection и servable variant lookup |
| `autplay_backup` | Backup-required read privileges |

Правила:

- application roles не владеют schemas;
- `PUBLIC` не имеет table/function access;
- GPU role не изменяет catalog, Vault blobs или user library;
- stream role не читает encrypted source URI;
- migration credentials не находятся в runtime API container.

Row-Level Security не является primary authorization v1. Перед публичным Multi-user mode проводится отдельный ADR: либо RLS как defense-in-depth с обязательным transaction user context, либо формально проверенная repository scoping policy.

---

# 7. Transaction boundaries

## 7.1. Local/device sync event apply

Одна server transaction:

```text
insert/check device_event_inbox
validate actor/device ownership
lock aggregate
apply domain change
append sync_event
update inbox status
advance acknowledged device sequence
commit
```

Повторный `event_id` возвращает сохраненный result. Тот же device sequence с другим request hash отклоняется.

## 7.2. Vault ingest

Database transaction не может атомарно commit filesystem bytes. Используется recoverable protocol:

1. fully write and fsync staging file;
2. verify decode, byte size and SHA-256;
3. reserve/find `vault_object` by SHA under transaction;
4. atomic rename into same-filesystem CAS destination;
5. fsync parent directory;
6. insert/update `VaultObject`, replica and AudioVariant metadata;
7. commit database;
8. reconciliation job исправляет orphan CAS file или stale STAGING row после crash.

Serving всегда вызывает `app_private.audio_variant_is_servable()` или эквивалентный repository query.

## 7.3. Merge

Одна serializable/locked transaction:

```text
lock source and target in deterministic UUID order
resolve redirect chains
create change set
coalesce user references
move approved catalog children
insert redirect
mark source MERGED
append audit and sync events
commit
```

---

# 8. Job queue protocol

Claim pattern:

```sql
WITH candidate AS (
    SELECT job_id
    FROM jobs.job
    WHERE state IN ('QUEUED', 'RETRY_WAIT')
      AND scheduled_at <= now()
    ORDER BY priority ASC, scheduled_at ASC, created_at ASC
    FOR UPDATE SKIP LOCKED
    LIMIT :batch_size
)
UPDATE jobs.job AS j
SET state = 'RUNNING',
    lease_owner = :worker_id,
    lease_deadline = now() + :lease_interval,
    heartbeat_at = now(),
    started_at = COALESCE(started_at, now()),
    attempt_count = attempt_count + 1
FROM candidate
WHERE j.job_id = candidate.job_id
RETURNING j.*;
```

`SKIP LOCKED` применяется только к queue-like access, не как общий способ скрыть contention. Основание: [PostgreSQL locking clause](https://www.postgresql.org/docs/18/sql-select.html#SQL-FOR-UPDATE-SHARE).

Worker обязан:

- heartbeat до половины lease interval;
- checkpoint до irreversible boundary;
- idempotent side effects;
- structured retry classification;
- bounded exponential backoff с jitter;
- очистить lease fields в terminal/retry transition;
- проверять `cancel_requested_at` в safe points.

---

# 9. Alembic migration layout

Reference DDL не переносится одной гигантской migration. Рекомендуемые revisions:

| Revision | Содержимое |
| --- | --- |
| `0001_extensions_schemas` | `pg_trgm`, `vector`, module schemas |
| `0002_account_catalog` | Users/devices, artists, recordings, releases |
| `0003_audit_identity` | Audit base, providers, external refs, redirects |
| `0004_sync_jobs` | Inbox/outbox, cursors, idempotency, job queue |
| `0005_library_playlists` | UserTrackRef, library, preferences, playlists |
| `0006_vault` | CAS metadata, replicas, variants, fingerprints |
| `0007_importing` | Import jobs, entries, candidates |
| `0008_ml_history` | Model registry, vectors, recommendations, listening events |
| `0009_constraints_triggers` | Cross-table constraints and private functions |
| `0010_indexes_privileges` | Search/queue indexes and role grants |

Каждая revision:

- имеет явные named constraints;
- проходит ручное review;
- не доверяет autogenerate как окончательному результату;
- содержит documented downgrade class;
- проходит `upgrade -> downgrade where supported -> upgrade` test;
- фиксируется в schema snapshot.

Alembic autogenerate не является полной проверкой всех PostgreSQL objects. Functions, triggers, extension state, expression/partial indexes и custom pgvector indexes проверяются отдельными tests.

---

# 10. Migration policy

## 10.1. Классы

| Класс | Пример | Rollback |
| --- | --- | --- |
| Additive | Nullable column, new table/index | Application rollback обычно безопасен |
| Expand/migrate/contract | Rename, type change, split field | Dual-read/write window, backfill, later contract |
| Derived rebuild | Embedding/index change | Switch alias/index, rebuild old if needed |
| Destructive | Drop data, merge irreversible dependency | Backup + explicit operator approval; downgrade may be unsupported |

## 10.2. Production rules

- Backup и restore preflight перед destructive/data migration.
- `lock_timeout` и `statement_timeout` задаются migration runbook.
- Большие indexes создаются `CONCURRENTLY` отдельной non-transactional Alembic step.
- `NOT VALID` FK/CHECK MAY использоваться для online validation, затем `VALIDATE CONSTRAINT`.
- Backfill chunked и resumable.
- App version compatibility проверяется до schema contract phase.
- Нельзя автоматически downgrade database после partial data migration.

---

# 11. Test matrix

## 11.1. Schema tests

| Test | Проверка |
| --- | --- |
| Clean install | Все revisions применяются в пустую PostgreSQL 18 DB |
| Alembic head | Единственная ожидаемая head |
| Drift | SQLAlchemy metadata не порождает неожиданный migration diff |
| Constraint names | PK/FK/UQ/CK имеют deterministic names |
| Extensions | Required compatible `pg_trgm` и `vector` доступны |
| Privileges | Runtime roles не имеют лишних DDL/read rights |

## 11.2. Integrity tests

- duplicate SHA-256 отклоняется;
- invalid hash length отклоняется;
- external reference с двумя targets отклоняется;
- device/user mismatch отклоняется;
- playlist entry другого user отклоняется;
- duplicate Track в playlist разрешен;
- duplicate active position запрещен;
- active UserTrackRef duplicate coalesces before FK update;
- canonical variant другого Recording отклоняется;
- embedding dimension/source mismatch отклоняется;
- redirect cycle отклоняется;
- job dependency cycle отклоняется;
- same idempotency key + different hash отклоняется;
- tombstone event другого user отклоняется;
- recommendation/listening cross-user reference отклоняется.

## 11.3. Job concurrency tests

- 2/4/8 workers не claim одну job дважды;
- expired lease возвращается в queue один раз;
- worker crash до/после checkpoint;
- cancellation в safe point;
- dependency success/terminal policy;
- priority fairness не допускает вечного starvation P3/P4;
- repeated completion command idempotent.

## 11.4. Migration tests

- upgrade от каждой поддерживаемой release;
- failure до backfill, во время backfill и после schema switch;
- old app + expanded schema;
- new app + expanded schema;
- playlist order and duplicate preservation;
- UUID/redirect preservation;
- import checkpoint preservation;
- rollback of active embedding alias/index;
- backup restore после migration.

## 11.5. Performance tests

Reference dataset:

```text
100 000 Recording
150 000 ReleaseTrack
200 000 AudioVariant
5 users
1 000 playlists
1 000 000 listening events
100 000 embeddings for one model
```

Measure:

- fuzzy search p95/p99;
- library cursor page;
- playlist ordered page;
- sync pull by user/sequence;
- job claim under concurrency;
- exact vector top-k;
- HNSW candidate recall/latency only after model benchmark;
- backup and restore duration.

---

# 12. Verification commands after repository creation

Python workflow uses `uv`:

```bash
uv sync --frozen
uv run alembic upgrade head
uv run alembic check
uv run pytest tests/unit/db -q
uv run pytest tests/integration/postgres -q
uv run pytest tests/integration/migrations -q
uv run ruff check server tests
uv run mypy server/src
```

Clean DDL smoke test:

```bash
psql -v ON_ERROR_STOP=1 "$AUTPLAY_TEST_DATABASE_URL" \
  -f docs/schema/AutPlay_PostgreSQL_Schema_v1.sql
```

`AUTPLAY_TEST_DATABASE_URL` должен указывать только на disposable test database.

---

# 13. Known deliberate omissions

Не входят в initial DDL:

- production HNSW index до выбора embedding dimension/model;
- time partitioning listening events до измерения объема;
- collaborative playlist membership;
- public SaaS tenant hierarchy;
- OpenSubsonic compatibility projections;
- Wave room state;
- lyrics storage;
- PostgreSQL RLS policy;
- database roles/passwords, зависящие от deployment environment;
- generated search normalization: normalization остается versioned application logic.

---

# 14. Acceptance checklist

Schema v1 готова к initial Alembic implementation, когда:

1. Reference DDL выполняется на pinned PostgreSQL 18 + pgvector image.
2. Все 52 tables создаются в clean database.
3. FK/CK/UQ и triggers проходят negative tests.
4. Device/user и playlist/library boundaries не обходятся прямым DML test role.
5. Job claim concurrency test не дает duplicate active leases.
6. Vault serving query не возвращает uncommitted/quarantined bytes.
7. Track Identity evidence fields сохраняются без потери version metadata.
8. Exact vector baseline измерен до создания ANN index.
9. Alembic schema snapshots и drift check добавлены в CI.
10. Backup/restore drill проходит после upgrade head.

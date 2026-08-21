# AutPlay PostgreSQL Schema v1 - Decisions, Migration Plan and Test Matrix

**Статус:** Draft for executable migration implementation  
**Версия:** 1.0  
**Reference DDL:** [AutPlay_PostgreSQL_Schema_v1.sql](<AutPlay_PostgreSQL_Schema_v1.sql>)  
**Основание:** `AutPlay ER Model v1`, `AutPlay System Architecture v1`, `AutPlay Track Identity v1`, accepted `ADR-015`

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
- immutable Identity Catalog registries, policy activation history, match decisions and candidate evidence;
- validated current-decision projections для `importing.import_entry` и `library.user_track_ref`;
- application transactions для domain commands и event emission.

Reference DDL содержит ровно 64 tables, 60 explicit `CREATE INDEX` objects, 15 helper/constraint functions и 43 non-internal trigger. PK/UNIQUE backing indexes в число 60 не входят.

Identity-history synchronization фиксирует только безопасное append-only хранение P00-D003. Frozen F-016 и отдельное решение P00-D004 не изменены: initial schema не содержит policy activation event и не разрешает pre-benchmark applied auto-match.

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
| Match query/decision JSON document | 128 KiB каждый |
| Match candidate evidence JSON document | 128 KiB каждый; суммарно не более 128 KiB на candidate row |
| Canonical candidate-evidence aggregate одного decision | 4 MiB |
| Source observation raw metadata | 1 MiB |
| Audit metadata | 64 KiB |

Крупные файлы и import inputs хранятся вне JSONB, по bounded reference.

For identity evidence, the database enforces JSON type/cardinality and bounds the
stored `jsonb::text` representation. The application sanitizer separately enforces
the per-field 128 KiB limits on exact RFC 8785 canonical bytes, records each
candidate document byte size, and supplies the canonical hashes; P02 must prove
N-1/N/N+1 and sensitive-field fixtures through that command boundary.

## 3.6. Вектор

Initial schema использует unbounded-dimension `vector` column и cross-table trigger, проверяющий `vector_dims()` против Model Registry.

Причина: embedding dimension пока не выбрана benchmark на RTX 3060 12 GB.

Initial queries:

1. exact cosine distance;
2. filter по active `embedding_model_id`;
3. bounded candidate count;
4. измерение p95/p99 и recall.

HNSW создается отдельной migration после выбора модели. Index должен быть dimension-specific и model-specific. Старый и новый indexes сосуществуют на rollback window.

## 3.7. Immutable identity decision history

P00-D003 заменяет draft `importing.match_candidate` шестью Identity Catalog tables:

1. `identity.matcher_release`;
2. `identity.calibrator_release`;
3. `identity.threshold_set`;
4. `identity.match_policy_activation`;
5. `identity.match_decision`;
6. `identity.match_candidate_evidence`.

Release/threshold rows, activation events, decisions и candidate evidence являются append-only. Matcher, calibrator и threshold references используют `ON DELETE RESTRICT`; re-score, review, conflict discovery и rollback добавляют successor row. Backward `supersedes_decision_id`/`supersedes_activation_id` сохраняет predecessor immutable, а unique predecessor reference запрещает concurrent branch.

`match_decision` хранит typed query identity, sanitized query snapshot/hash/version, `SHADOW|APPLIED`, все пять resolver states, actor/idempotency/lineage, matcher/calibrator/threshold snapshot, top-one/top-two scores и provider-independent explanation. `match_candidate_evidence` хранит ranks 1..100, exact feature/origin/extractor evidence и RFC 8785 hash. Deferred validation sealed snapshot требует contiguous ranks, exact candidate count/aggregate hash, selected/rank-1 equality и bidirectional rank-2/margin consistency.

`importing.import_entry` и `library.user_track_ref` получают nullable `current_match_decision_id`. Deferred projection validation допускает только matching typed query/owner, `APPLIED` mode и согласованные state/action/Recording fields. `SHADOW`, `INTEGRITY_CONFLICT` и `DEFERRED_EVIDENCE` не могут создавать resolution projection. Import payload cleanup очищает bounded raw payload, но не удаляет referenced query envelope/history.

Шесть новых explicit indexes:

- `ix_threshold_set_scope`;
- `ix_match_policy_activation_threshold_time`;
- `ix_match_decision_query_time`;
- `ix_match_decision_candidate_time`;
- `ix_match_decision_matcher_time`;
- `ix_match_candidate_evidence_recording`.

Три новые functions — `app_private.reject_identity_history_mutation()`, `app_private.validate_match_policy_activation()` и `app_private.validate_match_decision()`. Eight new triggers состоят из трех immutable-registry triggers, activation validator, двух deferred decision/evidence aggregate validators и двух deferred import/UserTrackRef projection validators. Эта P04 delta `-1 + 6` tables, `-1 + 6` explicit indexes, `+3` functions и `+8` triggers дала inventory 57/53/13/40; P06 `0011_vault_runtime` добавляет 2 tables, 4 indexes и 1 trigger, P09 `0012_sync_runtime` — 3 tables и 2 explicit indexes, P11 `0013_recommendation_runtime` — 2 tables, 1 explicit index, 2 immutable-row functions и 2 triggers, P12 `0014_gpu_enrichment` — 4 tables, 4 explicit indexes, 4 integrity/immutability functions и 6 triggers, а P13 `0015_wave_runtime` — 7 tables и 3 explicit indexes. Текущий exact physical inventory — 73/69/19/49.

---

# 4. Database schemas и ownership

| Schema | Write owner module |
| --- | --- |
| `account` | Identity/Auth |
| `catalog` | Music Catalog |
| `identity` | Identity Resolution: providers, immutable matcher/calibrator/threshold registries, activation history, decisions and candidate evidence |
| `library` | User Library/History |
| `playlist` | Playlist Engine |
| `vault` | Vault/Ingest |
| `importing` | Library Migration workflow and validated current-decision projection |
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
- embedding source variant принадлежит той же Recording;
- matcher, calibrator, threshold, policy activation, match decision и candidate evidence rows append-only;
- matcher/calibrator/threshold references и version snapshots согласованы;
- decision candidate set sealed точным count, contiguous ranks и aggregate evidence SHA-256;
- decision и activation supersession образуют единственную append-only chain без branch/cycle;
- applied `AUTO_MATCH` требует latest benchmark-backed active policy exact mode/tier scope, sufficient threshold/tier/margin и отсутствие hard conflict;
- import/UserTrackRef current pointers ссылаются только на owner/query/target-consistent `APPLIED` decisions.

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
| Query snapshot allowlist и RFC 8785 hashes | Canonicalization и sensitive-field rejection выполняются application adapter; DB проверяет shape, bounds, declared versions и 32-byte hashes |
| Identity decision command authorization | USER/ADMIN ownership и access к shared query object проверяются до insert; deferred DB validator повторно проверяет relational ownership |
| Atomic match projection | Decision, sealed candidate snapshot, audit и owner projection создаются одной transaction; deferred triggers отклоняют partial/divergent commit |
| Identity policy race | Activation/deactivation и applied SYSTEM decision используют один transaction-scoped advisory lock exact mode/tier scope |

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

## 7.4. Identity decision and projection

Одна command transaction:

```text
authorize actor and query object
take policy-scope advisory lock when activation or applied AUTO_MATCH is involved
insert immutable decision and complete candidate-evidence snapshot
append review/activation successor instead of updating history
update import/UserTrackRef current projection when execution_mode = APPLIED
append audit/sync effects owned by the command
run deferred decision/evidence/projection constraints
commit
```

`SHADOW` decision не меняет catalog/user/import projection. Re-score и manual review всегда создают новую row с backward `supersedes_decision_id`; conceptual `superseded_by` читается как inverse relation. Initial clean install содержит zero activation events.

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
| `0003_audit_identity` | Audit base; providers/external refs/redirects; immutable matcher, calibrator and threshold registries plus empty activation history |
| `0004_sync_jobs` | Inbox/outbox, cursors, idempotency, job queue |
| `0005_library_playlists` | UserTrackRef, library, preferences, playlists |
| `0006_vault` | CAS metadata, replicas, variants, fingerprints |
| `0007_importing_identity_history` | Import jobs/entries first; general immutable match decisions and candidate evidence; typed query/current-projection FKs added after both sides exist; no legacy `importing.match_candidate` |
| `0008_ml_history` | Model registry, vectors, recommendations, listening events |
| `0009_constraints_triggers` | Cross-table constraints, exactly 13 private helper/constraint functions and 40 non-internal triggers, including deferred identity aggregate/projection validation |
| `0010_indexes_privileges` | Exactly 53 explicit search/history/queue indexes and role grants; PK/UNIQUE backing indexes excluded from this inventory |
| `0011_vault_runtime` | P06 owner/device-bound resumable upload sessions, durable chunk receipts, commit/reuse/quarantine state links, and four operational indexes |
| `0012_sync_runtime` | P09 durable Journal lineage/terminal ACK state, materialized bootstrap snapshot rows, canonical interaction facts and two operational/semantic indexes |
| `0013_recommendation_runtime` | P11 immutable pipeline manifests and retained input snapshots; replay/provenance/owner-safe pack extensions; bounded retention and immutable-row triggers |
| `0014_gpu_enrichment` | P12 immutable model/benchmark/activation history and versioned embedding enrichment state; CPU runtime remains independent |
| `0015_wave_runtime` | P13 digest-only invite-scoped rooms, device membership, canonical queue, ordered commands, expiring preflight/timing and guarded non-destructive downgrade |

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
| Exact inventory | 64 tables, 60 explicit indexes, 15 `app_private` helper/constraint functions и 43 non-internal trigger; exact names совпадают с reference contract |
| Extensions | Required compatible `pg_trgm` и `vector` доступны |
| Privileges | Runtime roles не имеют лишних DDL/read rights |
| Identity initial state | Шесть identity-history tables существуют, legacy `importing.match_candidate` отсутствует, policy activation row count равен zero |

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

## 11.3. Identity history, policy and projection tests

Round-trip fixtures сохраняют без потери все normative decision fields, все пять resolver states, import/local-audio/external-reference query types, multiple candidate origins/ranks и неизвестные feature keys.

Обязательные negative/edge tests:

- `UPDATE` и `DELETE` отклоняются для каждой из шести history/registry tables;
- unknown release FK, matcher/calibrator/threshold cross-version mismatch, invalid actor/user pair, invalid resolver state, score range и margin mismatch отклоняются;
- typed query key отсутствует/не соответствует `query_type`, orphan, cross-user/device и cross-import query отклоняются;
- JSON shape/cardinality, sensitive-field allowlist и N-1/N/N+1 byte fixtures проверяются; RFC 8785 hashes воспроизводимы;
- candidate sets 0/1/2/100 round-trip; candidate 101, rank gap/duplicate, wrong count/hash, late/post-commit insert и selected/top-one/top-two mismatch отклоняются;
- `SHADOW + AUTO_MATCH` отклоняется; shadow counterfactual сохраняется только при resolver state `REVIEW_REQUIRED` и не меняет projection;
- applied auto-match отклоняется при inactive/mismatched policy, insufficient evidence tier/score/margin, nullable calibrator или любом hard conflict;
- pre-benchmark applied T4 отклоняется; это сохраняет frozen F-016 и не решает P00-D004;
- `INTEGRITY_CONFLICT` требует non-empty conflict reason и не меняет resolution projection; `DEFERRED_EVIDENCE` допускает nullable scores и также не меняет resolution projection;
- ACCEPT/REJECT target обязан принадлежать candidate evidence immediate predecessor; KEEP_UNRESOLVED и CREATE_RECORDING соблюдают утверждённые state/target/projection rules;
- CREATE_RECORDING требует newly-created Recording и atomic projection, но не создает global merge;
- self/cycle/cross-query/earlier-time/branch supersession отклоняется; re-score/review добавляет rows, не меняя old explanation;
- import cleanup сохраняет identity history, а удаление referenced query envelope отклоняется;
- import и UserTrackRef pointers отклоняют wrong type/owner, shadow decision и state/target-divergent projection;
- application command catches the unique scope/key collision, compares the stored request hash and returns the existing row only when hashes match; a different hash yields a stable conflict (raw direct SQL INSERT is expected to raise the named unique violation);
- provider-independent explanation читается при недоступном provider.

Policy lifecycle/concurrency tests:

- activation/rollback требует 32-byte benchmark hash, non-null calibrator, active OWNER/ADMIN и exact matcher/calibrator/mode/tier scope;
- activation sequence не допускает gap/branch, rollback не указывает на never-active set, а deactivate/rollback append events не переписывают registry rows;
- две concurrent decision successor transactions и две activation successor transactions оставляют ровно одного successor;
- deactivate-vs-applied-auto race сериализуется одним advisory lock и не допускает decision по stale active policy.

Deferred constraint failures проверяются через `SET CONSTRAINTS ALL IMMEDIATE` или реальный `COMMIT`, а не только внутри незавершенной transaction.

## 11.4. Job concurrency tests

- 2/4/8 workers не claim одну job дважды;
- expired lease возвращается в queue один раз;
- worker crash до/после checkpoint;
- cancellation в safe point;
- dependency success/terminal policy;
- priority fairness не допускает вечного starvation P3/P4;
- repeated completion command idempotent.

## 11.5. Migration tests

- upgrade от каждой поддерживаемой release;
- failure до backfill, во время backfill и после schema switch;
- old app + expanded schema;
- new app + expanded schema;
- playlist order and duplicate preservation;
- UUID/redirect preservation;
- import checkpoint preservation;
- clean `upgrade -> downgrade -> upgrade` сохраняет exact identity object inventory, names и metadata drift contract;
- rollback of active embedding alias/index;
- backup restore после migration.

## 11.6. Performance tests

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
- Wave public-Internet/TLS topology and cross-instance live fanout (P13 durable room state is in `0015_wave_runtime`);
- lyrics storage;
- PostgreSQL RLS policy;
- database roles/passwords, зависящие от deployment environment;
- generated search normalization: normalization остается versioned application logic;
- benchmark dataset/payload и active identity policy event: initial schema содержит только immutable registries/history и zero activations;
- identity matcher behavior и P00-D004 semantic decision; frozen F-016 остается неизменным.

---

# 14. Acceptance checklist

Schema v1 готова к initial Alembic implementation, когда:

1. Reference DDL выполняется на pinned PostgreSQL 18 + pgvector image.
2. Все 64 tables, 60 explicit indexes, 15 helper/constraint functions и 43 non-internal trigger создаются в clean database с exact names; legacy `importing.match_candidate` отсутствует.
3. FK/CK/UQ, append-only/deferred validators и projection triggers проходят negative tests.
4. Device/user и playlist/library boundaries не обходятся прямым DML test role.
5. Job claim concurrency test не дает duplicate active leases.
6. Vault serving query не возвращает uncommitted/quarantined bytes.
7. Track Identity decision/history round-trip сохраняет все пять states, version metadata, origins, unknown features, actor, lineage и provider-independent explanation; sealed candidate aggregate и policy/projection races проходят tests.
8. Exact vector baseline измерен до создания ANN index.
9. Alembic schema snapshots и drift check добавлены в CI.
10. Backup/restore drill проходит после upgrade head.
11. Initial identity activation history пуст; applied auto-match до benchmark отклоняется без изменения F-016 или P00-D004.

---

# 15. Frontend M4 stable Artist identity prerequisite

ADR-028 confirms existing `catalog.artist.artist_id` as the sole canonical Artist UUID. Alembic
`0016_artist_id_sync_contract` adds a live UTR reverse partial index, an active Release-credit
index, persisted bootstrap capabilities, a concurrency-safe 1,000-member credit bound, and a
child-change trigger that advances the parent credit row version. It performs no name backfill,
merge, deletion, or reinterpretation; credits without `artist_credit_name` rows remain unresolved.

---

# 16. M5B profile-pairing runtime delta

Alembic `0017_profile_pairing_runtime` adds the `account.server_instance`,
`account.enrollment_invitation`, `account.enrollment_exchange_receipt`, and
`account.session_rotation_receipt` evidence tables plus the active-invitation index. It adds
nullable device public-key thumbprints and v2 session lineage (`family_id`, `generation`).
`account.user_session.session_mode` is `LEGACY` for every existing P03 row and `V2` only for
device-PoP pairing sessions; the legacy refresh endpoint therefore cannot rotate a v2 session.
Alembic `0018_profile_lifecycle_cleanup` adds one durable lifecycle-command fact keyed by the
client operation UUID. It preserves actor, target, action, bounded reason, exact terminal outcome
and terminal instant; exact duplicate requests therefore return the original result rather than a
new timestamp. Its two receipt-expiry indexes support the CPU worker's bounded no-broker cleanup
cadence (at most one hour), which removes a receipt no later than 24 hours after its grace boundary.
The additive M5B delta changes the live inventory from 68 to 73 tables and 69 explicit indexes
while retaining 19 functions and 49 triggers.

---

# 17. M6 administrative web runtime delta

Alembic `0019_m6_web_admin_runtime` adds isolated, opaque-only browser invitation, login
challenge, web-session, predecessor-rotation evidence, terminal-receipt, and login-rate-window
state. Browser authority is never represented by the Android bearer/session tables. The five
expiry and active-state indexes make bounded cleanup and active-session lookup deterministic.
The additive M6 delta changes the live inventory to 79 tables and 77 explicit indexes while
retaining 19 functions and 49 triggers.

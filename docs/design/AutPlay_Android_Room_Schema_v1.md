# AutPlay Android Room Schema v1

**Статус:** Approved Room v1 contract for P05 implementation
**Версия:** 1.0  
**Database:** `autplay.db`, schema version 1  
**Preferred runtime:** Room 3.0.1, `androidx.room3`, KSP, Kotlin codegen  
**SQLite:** `BundledSQLiteDriver`, WAL  
**Основание:** `AutPlay System Architecture v1`, `AutPlay ER Model v1`, `AutPlay Track Identity v1`, `AutPlay Sync Protocol v1`, accepted `ADR-018`

---

# 1. Назначение

Документ фиксирует локальную Android database для local-first AutPlay.

Room хранит:

- локальную projection catalog;
- пользовательскую библиотеку и unresolved Track;
- playlists и устойчивый порядок;
- связи с локальными audio URI;
- playback queue snapshot;
- listening history;
- Offline Journal;
- local-only mutation outbox for pre-binding work;
- sync cursors, tombstones и conflicts;
- recommendation pack;
- производный полнотекстовый индекс.

Room не хранит:

- raw audio bytes;
- абсолютные filesystem paths;
- server Vault storage keys;
- access/refresh tokens;
- пароли и ключи в открытом виде;
- server job queue;
- ML model weights/embeddings v1;
- VPN state.

---

# 2. Runtime decision

## 2.1. Preferred stack

| Компонент | Решение |
| --- | --- |
| Room | 3.0.1 stable |
| Package | `androidx.room3` |
| Compiler | KSP, Kotlin generation only |
| API style | Coroutine/suspend + `Flow` |
| Driver | `BundledSQLiteDriver` |
| FTS | FTS5 through `@Fts5` |
| Journal mode | WAL |
| Schema export | Required and committed to git |

Причины:

- AutPlay является greenfield Kotlin project;
- Room 3 не требует legacy `SupportSQLite` API;
- все database operations coroutine-based;
- FTS5 доступен как first-class entity;
- Bundled SQLite одинаков на поддерживаемых Android versions;
- миграция с Room 2.x не требуется, если выбрать Room 3 до первой public/local data release.

## 2.2. Compatibility gate

До фиксации первой пользовательской schema выполняется spike:

1. Room 3 + selected Kotlin/KSP/AGP compile;
2. Media3 и WorkManager работают в том же application;
3. `BundledSQLiteDriver` загружается на physical Samsung A55 и minimum-SDK emulator;
4. FTS5 query и migration test проходят;
5. release build/R8 запускается;
6. database open, WAL checkpoint и process restart проходят instrumentation test.

Если найден блокирующий ecosystem defect, fallback на Room 2.8.4 допустим только до появления database version 1 у пользователя. После этого смена Room major требует отдельный migration ADR.

Официальные основания:

- [Room 3.0 release notes](https://developer.android.com/jetpack/androidx/releases/room3)
- [Room entities and FTS](https://developer.android.com/training/data-storage/room/defining-data)
- [Room migrations](https://developer.android.com/training/data-storage/room/migrating-db-versions)
- [Bundled SQLite driver](https://developer.android.com/kotlin/multiplatform/room)

---

# 3. Android platform baseline

Preliminary application baseline:

```text
minSdk = 26
targetSdk = current stable at repository bootstrap
compileSdk = current stable at repository bootstrap
```

Room 3 compatibility проверяется на `minSdk = 26`. Повышение minSdk после первой release является product decision. Понижать ниже 26 без device/performance matrix не требуется.

---

# 4. Общие соглашения

## 4.1. Идентификаторы

V1 хранит UUID как canonical lowercase `TEXT`:

```text
xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

Причина выбора TEXT вместо 16-byte BLOB:

- проще диагностика и export;
- нет опасной `ByteArray` equality semantics в Kotlin entities;
- прямое соответствие JSON/OpenAPI IDs;
- ожидаемый local scale приемлем;
- listening history имеет retention/compaction.

Переход на BLOB UUID возможен только после storage/index benchmark и полноценной migration.

## 4.2. Local и server ID

Local-first entity не меняет primary key после sync.

```text
local_id TEXT PRIMARY KEY
server_id TEXT NULL UNIQUE
```

- `local_id` создается на устройстве до server connection;
- `server_id` заполняется после ACK/bootstrap;
- server merge обновляет `server_id`/redirect projection, но не переписывает local references в середине transaction;
- event idempotency использует стабильный local aggregate ID.

## 4.3. Время и duration

- timestamps: epoch milliseconds в SQLite `INTEGER`;
- duration/progress: milliseconds в `INTEGER` (`Long`);
- device `occurred_at` и server time не смешиваются;
- monotonic playback clock не сохраняется как wall-clock timestamp.

## 4.4. Boolean

Kotlin `Boolean` -> SQLite `INTEGER NOT NULL`, values `0/1`.

## 4.5. Enum/state

Persistence entity хранит raw `String`, а не Kotlin enum converter, который падает на новом server value.

Domain mapping:

```text
Known(value)
Unknown(rawValue)
```

Unknown value сохраняется и не приводит к удалению row.

## 4.6. JSON

Versioned payload хранится как `TEXT`:

```text
payload_version INTEGER
payload_json TEXT
```

Serialization детерминирована для request hash. Максимальный payload Offline Journal - 256 KiB.

## 4.7. Hashes

SHA-256 хранится как `BLOB` длиной 32 bytes. Проверка длины выполняется TypeConverter/value object и repository tests.

---

# 5. Sync columns pattern

Изменяемые sync-able entities содержат:

| Column | SQLite | Назначение |
| --- | --- | --- |
| `local_id` | TEXT PK | Стабильный device UUID |
| `server_id` | TEXT NULL UNIQUE | Server UUID после sync |
| `server_row_version` | INTEGER NULL | Optimistic concurrency/ETag |
| `sync_state` | TEXT | LOCAL_ONLY, CLEAN, DIRTY, CONFLICT, DELETED |
| `last_local_sequence` | INTEGER | Последнее локальное event sequence |
| `server_updated_at_ms` | INTEGER NULL | Информационная server time |
| `created_at_ms` | INTEGER | Local created time |
| `updated_at_ms` | INTEGER | Local updated time |

Server projection не перезаписывает row со `sync_state IN ('DIRTY', 'CONFLICT')` напрямую. Он создает merge input или conflict record.

---

# 6. Catalog projection

Android не копирует полный server catalog. Projection содержит только данные, необходимые для текущей библиотеки, playlist, queue, history и offline pack.

## 6.1. `recording_projection`

| Column | Type | Constraint/meaning |
| --- | --- | --- |
| `local_recording_id` | TEXT | PK |
| `server_recording_id` | TEXT | NULL, unique index |
| `redirect_server_recording_id` | TEXT | NULL |
| `title` | TEXT | NOT NULL |
| `normalized_title` | TEXT | NOT NULL |
| `display_artist` | TEXT | NOT NULL |
| `normalized_artist` | TEXT | NOT NULL |
| `artist_credit_json` | TEXT | Versioned compact credit |
| `duration_ms` | INTEGER | NULL, > 0 by domain validation |
| `recording_kind` | TEXT | NOT NULL, unknown-safe |
| `version_text` | TEXT | NULL |
| `explicit_state` | INTEGER | -1 unknown, 0 false, 1 true |
| `artwork_ref` | TEXT | NULL, cache reference only |
| `catalog_version` | INTEGER | Server projection version |
| `projection_updated_at_ms` | INTEGER | NOT NULL |
| `is_deleted` | INTEGER | NOT NULL, default 0 |

Indexes:

- unique `server_recording_id`;
- `(normalized_artist, normalized_title)`;
- `(projection_updated_at_ms)`;
- `(redirect_server_recording_id)`.

SQLite допускает несколько `NULL` в unique index, поэтому partial index здесь не нужен.

## 6.2. `release_projection`

| Column | Type | Constraint/meaning |
| --- | --- | --- |
| `local_release_id` | TEXT | PK |
| `server_release_id` | TEXT | NULL, unique |
| `server_release_group_id` | TEXT | NULL |
| `title` | TEXT | NOT NULL |
| `display_artist` | TEXT | NOT NULL |
| `release_date_text` | TEXT | NULL, preserves precision |
| `release_type` | TEXT | NULL |
| `artwork_ref` | TEXT | NULL |
| `catalog_version` | INTEGER | NOT NULL |
| `projection_updated_at_ms` | INTEGER | NOT NULL |
| `is_deleted` | INTEGER | NOT NULL |

## 6.3. `release_track_projection`

| Column | Type | Constraint/meaning |
| --- | --- | --- |
| `local_release_track_id` | TEXT | PK |
| `server_release_track_id` | TEXT | NULL, unique |
| `local_release_id` | TEXT | FK release, RESTRICT |
| `local_recording_id` | TEXT | FK recording, RESTRICT |
| `medium_position` | INTEGER | >= 1 |
| `sequence_no` | INTEGER | >= 1 |
| `number_text` | TEXT | NULL |
| `credited_title` | TEXT | NOT NULL |
| `credited_artist` | TEXT | NOT NULL |
| `duration_ms` | INTEGER | NULL |

Unique server ID, plus indexes on `local_release_id` and `local_recording_id`.

---

# 7. UserTrackRef and library

## 7.1. `user_track_ref`

| Column | Type | Constraint/meaning |
| --- | --- | --- |
| `local_user_track_ref_id` | TEXT | PK, stable aggregate ID |
| `server_user_track_ref_id` | TEXT | NULL, unique |
| `local_recording_id` | TEXT | NULL FK projection |
| `server_recording_id` | TEXT | NULL, cached canonical target |
| `resolution_status` | TEXT | UNRESOLVED/CANDIDATES/RESOLVED/AMBIGUOUS/NOT_FOUND/unknown |
| `raw_title` | TEXT | NULL |
| `raw_artist` | TEXT | NULL |
| `raw_album` | TEXT | NULL |
| `raw_duration_ms` | INTEGER | NULL |
| `resolution_confidence` | REAL | NULL |
| `sync_state` | TEXT | NOT NULL |
| `server_row_version` | INTEGER | NULL |
| `last_local_sequence` | INTEGER | NOT NULL |
| `created_at_ms` | INTEGER | NOT NULL |
| `updated_at_ms` | INTEGER | NOT NULL |
| `deleted_at_ms` | INTEGER | NULL |

Indexes:

- unique `server_user_track_ref_id`;
- unique `server_recording_id`;
- `(resolution_status, updated_at_ms)`;
- `(sync_state, updated_at_ms)`.

Удаленный ref не дублируется новой row: повторное добавление восстанавливает тот же lifecycle row и сохраняет стабильный local ID.

## 7.2. `user_track_external_ref`

Composite PK:

```text
local_user_track_ref_id
provider_key
external_entity_type
external_id
market_scope
```

Additional columns:

```text
relation_role
first_seen_at_ms
```

FK to `user_track_ref` uses CASCADE because row is a pure association. External ID string is not treated as Recording identity without server resolution.

## 7.3. `library_entry`

| Column | Type | Constraint/meaning |
| --- | --- | --- |
| `local_library_entry_id` | TEXT | PK |
| `server_library_entry_id` | TEXT | NULL, unique |
| `local_user_track_ref_id` | TEXT | FK RESTRICT |
| `added_at_ms` | INTEGER | NOT NULL |
| `source` | TEXT | NOT NULL |
| `availability_status` | TEXT | NOT NULL |
| `sync_state` | TEXT | NOT NULL |
| `server_row_version` | INTEGER | NULL |
| `last_local_sequence` | INTEGER | NOT NULL |
| `removed_at_ms` | INTEGER | NULL |
| `updated_at_ms` | INTEGER | NOT NULL |

Unique index на `local_user_track_ref_id`. Повторное добавление восстанавливает существующую row и очищает `removed_at_ms`, а не создает второй lifecycle row.

## 7.4. `user_track_preference`

| Column | Type |
| --- | --- |
| `local_user_track_ref_id` | TEXT PK/FK |
| `preference` | TEXT |
| `rating` | INTEGER NULL |
| `excluded_from_taste` | INTEGER |
| `sync_state` | TEXT |
| `last_local_sequence` | INTEGER |
| `updated_at_ms` | INTEGER |

---

# 8. Playlists

## 8.1. `playlist`

| Column | Type |
| --- | --- |
| `local_playlist_id` | TEXT PK |
| `server_playlist_id` | TEXT NULL unique |
| `name` | TEXT NOT NULL |
| `description` | TEXT NULL |
| `visibility` | TEXT NOT NULL |
| `playlist_type` | TEXT NOT NULL |
| `smart_rule_version` | INTEGER NULL |
| `smart_rule_json` | TEXT NULL |
| `sync_state` | TEXT NOT NULL |
| `server_row_version` | INTEGER NULL |
| `last_local_sequence` | INTEGER NOT NULL |
| `created_at_ms` | INTEGER NOT NULL |
| `updated_at_ms` | INTEGER NOT NULL |
| `deleted_at_ms` | INTEGER NULL |

## 8.2. `playlist_entry`

| Column | Type |
| --- | --- |
| `local_playlist_entry_id` | TEXT PK |
| `server_playlist_entry_id` | TEXT NULL unique |
| `local_playlist_id` | TEXT FK RESTRICT |
| `local_user_track_ref_id` | TEXT FK RESTRICT |
| `position_key` | TEXT NOT NULL |
| `active_position_key` | TEXT NULL |
| `source_position` | INTEGER NULL |
| `added_at_ms` | INTEGER NOT NULL |
| `sync_state` | TEXT NOT NULL |
| `server_row_version` | INTEGER NULL |
| `last_local_sequence` | INTEGER NOT NULL |
| `removed_at_ms` | INTEGER NULL |

Не создавать unique `(playlist, user_track_ref)`: duplicate Track entry разрешен.

Room-portable unique index:

```text
(local_playlist_id, active_position_key)
```

Для active entry `active_position_key = position_key`; при tombstone поле становится `NULL`. SQLite допускает несколько `NULL` в unique index, поэтому active order uniqueness выражается стандартной Room annotation без ручного partial index. Repository меняет `position_key`, `active_position_key` и `removed_at_ms` одной transaction и проверяет invariant в tests.

Position algorithm - lexicographic fractional token. Rebalance является отдельным versioned playlist operation, а не массовой скрытой перенумерацией при каждом insert.

---

# 9. Local audio state

## 9.1. `local_audio_state`

| Column | Type | Meaning |
| --- | --- | --- |
| `local_audio_state_id` | TEXT | PK |
| `local_user_track_ref_id` | TEXT | FK RESTRICT |
| `local_recording_id` | TEXT | NULL FK |
| `server_audio_variant_id` | TEXT | NULL |
| `content_uri` | TEXT | MediaStore/SAF URI |
| `persisted_uri_permission` | INTEGER | 0/1 |
| `local_sha256` | BLOB | NULL, 32 bytes |
| `fingerprint_algorithm` | TEXT | NULL |
| `fingerprint_version` | TEXT | NULL |
| `fingerprint_payload` | BLOB | NULL |
| `codec` | TEXT | NULL |
| `container` | TEXT | NULL |
| `bitrate_bps` | INTEGER | NULL |
| `sample_rate_hz` | INTEGER | NULL |
| `channels` | INTEGER | NULL |
| `duration_ms` | INTEGER | NULL |
| `status` | TEXT | AVAILABLE/MISSING/CORRUPT/VERIFYING/unknown |
| `storage_class` | TEXT | PINNED/USER_DOWNLOAD/PROACTIVE_CACHE/STREAM_CACHE |
| `byte_size` | INTEGER | NULL |
| `last_accessed_at_ms` | INTEGER | NULL |
| `last_verified_at_ms` | INTEGER | NULL |
| `created_at_ms` | INTEGER | NOT NULL |
| `updated_at_ms` | INTEGER | NOT NULL |

Indexes:

- unique `content_uri`;
- `local_sha256` non-unique;
- `(local_user_track_ref_id, status)`;
- `(storage_class, last_accessed_at_ms)` для eviction policy.

`content_uri` никогда не отправляется как Vault key. После restore/rescan URI валидируется, а не считается вечным.

---

# 10. Download ownership

Media3 DownloadManager/DownloadIndex является source of truth для byte progress и download execution.

Room хранит `download_intent`, а не дублирующий Download Manager.

## 10.1. `download_intent`

| Column | Type |
| --- | --- |
| `download_intent_id` | TEXT PK |
| `local_user_track_ref_id` | TEXT FK RESTRICT |
| `server_audio_variant_id` | TEXT NULL |
| `media3_download_id` | TEXT NULL unique |
| `desired_storage_class` | TEXT NOT NULL |
| `quality_policy` | TEXT NOT NULL |
| `source_policy` | TEXT NOT NULL |
| `state` | TEXT NOT NULL |
| `failure_code` | TEXT NULL |
| `created_at_ms` | INTEGER NOT NULL |
| `updated_at_ms` | INTEGER NOT NULL |
| `completed_at_ms` | INTEGER NULL |

State projection обновляется callback/reconciliation из Media3. Room progress bar MAY cache последнее значение в memory/UI state, но не объявляет его durable truth.

---

# 11. Playback queue

## 11.1. `queue_snapshot`

| Column | Type |
| --- | --- |
| `queue_snapshot_id` | TEXT PK |
| `queue_type` | TEXT: USER/WAVE/PLAYLIST/SEARCH |
| `source_context_id` | TEXT NULL |
| `current_entry_id` | TEXT NULL |
| `current_position_ms` | INTEGER NOT NULL |
| `shuffle_mode` | TEXT NOT NULL |
| `repeat_mode` | TEXT NOT NULL |
| `seed` | INTEGER NULL |
| `generation_version` | TEXT NULL |
| `is_active` | INTEGER NOT NULL |
| `active_slot` | TEXT NULL unique |
| `created_at_ms` | INTEGER NOT NULL |
| `updated_at_ms` | INTEGER NOT NULL |

Active snapshot имеет `active_slot = 'ACTIVE'`; inactive snapshots имеют `NULL`. SQLite допускает несколько `NULL` в unique index, поэтому V1 гарантирует не более одного active snapshot без manual/partial index. Repository меняет `is_active` и `active_slot` одной transaction.

## 11.2. `queue_entry`

| Column | Type |
| --- | --- |
| `queue_entry_id` | TEXT PK |
| `queue_snapshot_id` | TEXT FK CASCADE |
| `local_user_track_ref_id` | TEXT FK RESTRICT |
| `position` | INTEGER NOT NULL |
| `source_origin` | TEXT NOT NULL |
| `recommendation_request_id` | TEXT NULL |
| `source_audio_policy` | TEXT NOT NULL |
| `created_at_ms` | INTEGER NOT NULL |

Unique `(queue_snapshot_id, position)`. Duplicate Track разрешен.

---

# 12. Listening history

## 12.1. `listening_event`

| Column | Type |
| --- | --- |
| `listening_event_id` | TEXT PK, event UUID |
| `local_user_track_ref_id` | TEXT FK RESTRICT |
| `server_recording_id` | TEXT NULL |
| `started_at_ms` | INTEGER NOT NULL |
| `played_ms` | INTEGER NOT NULL |
| `track_duration_ms` | INTEGER NULL |
| `completion_ratio` | REAL NULL |
| `event_origin` | TEXT NOT NULL |
| `context` | TEXT NOT NULL |
| `recommendation_request_id` | TEXT NULL |
| `explicit_feedback` | TEXT NOT NULL |
| `excluded_from_taste` | INTEGER NOT NULL |
| `sync_state` | TEXT NOT NULL |
| `created_at_ms` | INTEGER NOT NULL |

Index `(started_at_ms DESC)` и `(sync_state, created_at_ms)`.

Playback создаёт событие один раз на logical play session. Seek events не создают отдельные fake listens. Replay создает новое событие.

---

# 13. Offline Journal

## 13.1. `offline_journal_event`

| Column | Type | Constraint/meaning |
| --- | --- | --- |
| `event_id` | TEXT | PK, stable retry ID |
| `journal_lineage_id` | TEXT | NOT NULL; composite FK with copied user/device to `journal_lineage`, RESTRICT |
| `idempotency_key` | TEXT | NOT NULL, stable retry key inside lineage |
| `user_id` | TEXT | NOT NULL, copied from immutable lineage |
| `device_id` | TEXT | NOT NULL, copied from immutable lineage |
| `server_profile_id` | TEXT | NOT NULL, immutable wrong-profile guard |
| `device_sequence` | INTEGER | Monotonically increasing inside lineage |
| `event_type` | TEXT | NOT NULL |
| `schema_version` | INTEGER | >= 1 |
| `aggregate_type` | TEXT | NOT NULL |
| `aggregate_local_id` | TEXT | NOT NULL |
| `aggregate_server_id` | TEXT | NULL |
| `base_server_row_version` | INTEGER | NULL |
| `payload_json` | TEXT | NOT NULL, <= 256 KiB |
| `request_hash` | BLOB | 32 bytes |
| `occurred_at_ms` | INTEGER | NOT NULL |
| `state` | TEXT | PENDING/SENDING/ACKED/CONFLICT/DEAD_LETTER |
| `attempt_count` | INTEGER | >= 0 |
| `next_attempt_at_ms` | INTEGER | NULL |
| `lease_token` | TEXT | NULL, process-local recovery token |
| `lease_expires_at_ms` | INTEGER | NULL |
| `last_error_code` | TEXT | NULL |
| `acked_at_ms` | INTEGER | NULL |

Indexes:

- unique `(journal_lineage_id, device_sequence)`;
- unique `(journal_lineage_id, idempotency_key)`;
- `(journal_lineage_id, state, next_attempt_at_ms, device_sequence)`;
- `(journal_lineage_id, aggregate_type, aggregate_local_id, device_sequence)`.

`event_id`, binding, aggregate identity, payload, hash and sequence are immutable after insert. Queries,
leases, recovery and terminal state transitions are lineage-scoped and state/token guarded.
SQLite enforces `(journal_lineage_id, user_id, device_id)` against the referenced lineage, so a DAO
caller cannot persist a wire binding that contradicts its sequence owner.

## 13.2. `journal_lineage` and sequence allocation

Device sequence выделяется в той же Room write transaction, что domain change и journal insert.

| Column | Type | Constraint/meaning |
| --- | --- | --- |
| `lineage_id` | TEXT | PK, local UUID |
| `user_id` | TEXT | NOT NULL |
| `device_id` | TEXT | NOT NULL, UNIQUE under the current server sequence contract |
| `journal_epoch` | TEXT | NOT NULL, UNIQUE |
| `next_device_sequence` | INTEGER | NOT NULL, next unallocated value, initially 1 |
| `created_at_ms` | INTEGER | NOT NULL |

Lineage tuple `(user_id, device_id, journal_epoch)` is immutable. Recreated local profiles resolving
to the same authenticated tuple reuse one lineage, counter and pending-event set. `server_profile_id`
is not a counter key. Under the current server uniqueness rule, the same `device_id` cannot restart
at sequence 1 with a new epoch; reset requires a new device identity until P09 owns an explicit
server cursor/constraint migration.

Repository increments and reads `next_device_sequence`, then writes domain state and the immutable
event inside one Room transaction. A failed transaction rolls back the counter increment and cannot
create a gap.

## 13.3. `local_mutation_outbox`

This table stores a local intent created before any authenticated server binding. It is not a P04
client event and therefore has no wire event ID, device sequence or request hash.

| Column | Type | Constraint/meaning |
| --- | --- | --- |
| `local_change_id` | TEXT | PK, stable local retry ID |
| `event_type` | TEXT | NOT NULL |
| `schema_version` | INTEGER | NOT NULL, >= 1 |
| `aggregate_type` | TEXT | NOT NULL |
| `aggregate_local_id` | TEXT | NOT NULL |
| `payload_json` | TEXT | NOT NULL, canonical and <= 262,144 UTF-8 bytes |
| `occurred_at_ms` | INTEGER | NOT NULL |
| `materialization_state` | TEXT | `UNMATERIALIZED` or `MATERIALIZED`; unknown values preserved |
| `materialized_event_id` | TEXT | NULL, UNIQUE FK `offline_journal_event.event_id`, RESTRICT |
| `materialized_at_ms` | INTEGER | NULL |

Indexes:

- `(materialization_state, occurred_at_ms)`;
- `(aggregate_type, aggregate_local_id, occurred_at_ms)`;
- unique `materialized_event_id`.

P05 accepts only the versioned allowlist `(USER_TRACK_REF_CREATED, 1, USER_TRACK_REF)` with exact
payload fields `artist`, `library_entry_local_id`, and `title`. Insertion and materialization both
enforce canonical JSON, bounded depth, safe recursive property names, forbidden sensitive segments,
no raw audio/path/credential material and the P04 payload byte limit. Unknown rows remain readable
and byte-preserved but fail closed for materialization.

Standalone domain/search/outbox insertion is one Room transaction. Explicit materialization is a
separate one-way transaction that allocates a lineage sequence, inserts a new immutable P04 event,
updates the domain correlation sequence and links the outbox row. It uses the stored payload rather
than mutable domain fields. A committed-result-loss retry returns the already linked event without a
second allocation; any pre-commit failure rolls back the link, event, domain correlation and counter.
P09 owns consent, authenticated binding revalidation, transport and reset; it never rewrites a P05
event in place.

## 13.4. Event state recovery

- `SENDING` с истекшим lease возвращается в `PENDING`;
- network timeout не меняет event ID/hash;
- ACK и cursor применяются одной transaction;
- different payload при том же event ID является local integrity error;
- `DEAD_LETTER` не удаляется молча и показывается в Sync Status.

---

# 14. Sync state

## 14.1. `sync_cursor`

| Column | Type |
| --- | --- |
| `server_profile_id` | TEXT PK |
| `journal_lineage_id` | TEXT NOT NULL; composite FK with device/epoch to `journal_lineage`, RESTRICT |
| `device_id` | TEXT NOT NULL |
| `journal_epoch` | TEXT NOT NULL |
| `opaque_cursor` | TEXT NULL |
| `last_pulled_server_sequence` | INTEGER NOT NULL |
| `last_acked_device_sequence` | INTEGER NOT NULL |
| `bootstrap_snapshot_id` | TEXT NULL |
| `bootstrap_state` | TEXT NOT NULL |
| `last_sync_at_ms` | INTEGER NULL |
| `updated_at_ms` | INTEGER NOT NULL |

Only the repository writes binding columns and requires `device_id`/`journal_epoch` equality with
the referenced lineage; SQLite enforces the same equality through the composite FK. Multiple
recreated local profiles may point to the same lineage while
cursor/bootstrap state remains profile-local. Credentials и base URL хранятся не здесь:
token/private material находится в Android Keystore-backed storage; non-secret server settings - в
DataStore.

## 14.2. `tombstone`

| Column | Type |
| --- | --- |
| `tombstone_id` | TEXT PK |
| `aggregate_type` | TEXT NOT NULL |
| `aggregate_local_id` | TEXT NOT NULL |
| `aggregate_server_id` | TEXT NULL |
| `deleted_by_event_id` | TEXT NOT NULL |
| `deleted_at_ms` | INTEGER NOT NULL |
| `retain_until_ms` | INTEGER NOT NULL |
| `server_acked` | INTEGER NOT NULL |

Unique `(aggregate_type, aggregate_local_id)`.

## 14.3. `sync_conflict`

| Column | Type |
| --- | --- |
| `sync_conflict_id` | TEXT PK |
| `aggregate_type` | TEXT NOT NULL |
| `aggregate_local_id` | TEXT NOT NULL |
| `local_event_id` | TEXT NULL |
| `server_event_id` | TEXT NULL |
| `reason_code` | TEXT NOT NULL |
| `local_snapshot_json` | TEXT NULL |
| `server_snapshot_json` | TEXT NULL |
| `status` | TEXT OPEN/RESOLVED/DISMISSED |
| `resolution_json` | TEXT NULL |
| `created_at_ms` | INTEGER NOT NULL |
| `resolved_at_ms` | INTEGER NULL |

Raw secrets/source URLs не попадают в snapshots.

---

# 15. Offline Recommendation Pack

## 15.1. `recommendation_pack`

| Column | Type |
| --- | --- |
| `offline_pack_id` | TEXT PK |
| `server_profile_id` | TEXT NOT NULL |
| `owner_user_id` | TEXT NULL only for legacy pre-v9 rows; such rows fail closed |
| `catalog_snapshot` | INTEGER NOT NULL |
| `model_bundle_version` | TEXT NOT NULL |
| `payload_version` | INTEGER NOT NULL |
| `payload_encoding` | TEXT NOT NULL |
| `payload` | BLOB NOT NULL |
| `payload_sha256` | BLOB NOT NULL, 32 bytes |
| `created_at_ms` | INTEGER NOT NULL |
| `expires_at_ms` | INTEGER NOT NULL |

Pack является candidate data, а не authorization на stream. P11 принимает только exact
profile/user/device-bound canonical `RAW_JSON` v1 с совпадающим SHA-256, известной encoding/version
и допустимым сроком. Expired pack MAY использоваться только для bounded offline local
recommendations по explicit labeled stale-fallback policy.

## 15.2. `recommendation_presentation`

| Column | Type |
| --- | --- |
| `server_profile_id` | TEXT NOT NULL |
| `owner_user_id` | TEXT NOT NULL |
| `presentation_id` | TEXT NOT NULL |
| `recommendation_request_id` | TEXT NOT NULL |
| `source_rank` | INTEGER NOT NULL |
| `impression_event_id` | TEXT NOT NULL UNIQUE |
| `recording_id` | TEXT NOT NULL |
| `offline_pack_id` | TEXT NULL |
| `source` | TEXT NOT NULL |
| `surface` | TEXT NOT NULL |
| `section_key` | TEXT NULL |
| `display_position` | INTEGER NOT NULL |
| `created_at_ms` | INTEGER NOT NULL |

Composite primary key is `(server_profile_id, owner_user_id, presentation_id,
recommendation_request_id, source_rank)`. Repository first finds or creates this mapping, then
allocates the existing Journal lineage and inserts the canonical P04 impression in one Room
transaction. Recomposition/restart therefore reuses `impression_event_id`; local reranking may
change only `display_position`, never server `source_rank` or request identity.

---

# 16. Full-text search

## 16.1. `track_search_content`

Derived content table:

| Column | Type |
| --- | --- |
| `rowid` | INTEGER PRIMARY KEY |
| `local_user_track_ref_id` | TEXT UNIQUE |
| `title` | TEXT |
| `artist` | TEXT |
| `album` | TEXT |
| `aliases` | TEXT |
| `transliterations` | TEXT |

## 16.2. `track_search_fts`

Room 3 entity:

```kotlin
@Fts5(
    contentEntity = TrackSearchContentEntity::class,
    tokenizer = FtsOptions.TOKENIZER_UNICODE61,
)
@Entity(tableName = "track_search_fts")
data class TrackSearchFtsEntity(
    @PrimaryKey
    @ColumnInfo(name = "rowid")
    val rowId: Long,
    val title: String,
    val artist: String,
    val album: String,
    val aliases: String,
    val transliterations: String,
)
```

Application пишет только `track_search_content`; созданные Room external-content triggers обновляют FTS в той же transaction. FTS является derived: migration или integrity action может полностью rebuild index.

Query rules:

- user input преобразуется в safe bound query, а не вставляется как raw SQL;
- token count/length ограничены;
- prefix search включается только для bounded tokens;
- ranking объединяет FTS score и deterministic exact-prefix boosts;
- empty query не сканирует всю FTS table без page limit.

---

# 17. Foreign-key policy

| Parent -> child | Delete action |
| --- | --- |
| UserTrackRef -> external refs | CASCADE |
| QueueSnapshot -> QueueEntry | CASCADE |
| RecordingProjection -> ReleaseTrackProjection | RESTRICT |
| UserTrackRef -> Library/Playlist/Audio/History | RESTRICT или tombstone command |
| Playlist -> PlaylistEntry | RESTRICT; domain delete writes entry tombstones |
| Search content -> FTS | Derived/rebuildable |

Hard delete не используется для sync-able user content до retention/ACK. Room entity FK включены и тестируются.

---

# 18. Required transactions

## 18.1. Add to library

Одна `withWriteTransaction`:

```text
upsert UserTrackRef
insert/restore LibraryEntry
update search content
allocate device sequence
append OfflineJournalEvent
commit
```

## 18.2. Playlist reorder

```text
read adjacent active position keys
calculate new lexicographic key
update one PlaylistEntry
allocate sequence
append event with base server row version
commit
```

Rebalance - отдельная bounded transaction/event with deterministic mapping.

## 18.3. Server pull apply

```text
load cursor
deduplicate server event IDs in applied-event window
resolve entity and pending local state
apply projection or create conflict
apply tombstone
advance cursor
commit
```

Cursor не продвигается, если event batch применен частично.

## 18.4. Download completion

```text
reconcile Media3 DownloadIndex
validate content URI
update LocalAudioState
update Library availability
append Vault reconciliation event if server sync enabled
commit
```

---

# 19. DAO boundaries

| DAO | Write ownership |
| --- | --- |
| `CatalogProjectionDao` | Server projection/import reconciliation only |
| `LibraryDao` | Library use cases |
| `PlaylistDao` | Playlist use cases |
| `LocalAudioDao` | Scan/download/integrity use cases |
| `QueueDao` | Playback queue persistence |
| `HistoryDao` | Listening session finalization |
| `JournalDao` | Sync engine; no UI direct writes |
| `SyncDao` | Cursor/tombstone/conflict apply |
| `RecommendationPackDao` | Pack verifier/recommender |
| `SearchDao` | Read-only FTS queries; rebuild through projection use case |

UI не вызывает multi-table writes через отдельные DAO самостоятельно. Application use case владеет transaction.

DAO правила:

- writes - `suspend`;
- observable reads - `Flow`;
- pagination - Paging 3 where list size can grow;
- no `SELECT *` in cross-entity projection queries;
- bounded result sizes;
- no blocking database calls on main thread;
- raw query только через allowlisted query builder and bound parameters.

---

# 20. WorkManager and Media3 ownership

| Работа | Владелец |
| --- | --- |
| Long media download | Media3 DownloadService/DownloadManager |
| Sync push/pull | WorkManager, durable payload in Room journal |
| Local rescan | WorkManager, checkpoint in Room if needed |
| Metadata refresh | WorkManager |
| Player/session | Media3 service |
| Download bytes/progress | Media3 DownloadIndex |
| User-visible desired download policy | Room `download_intent` |

WorkManager input data не содержит крупный event/import payload. Он содержит только stable ID для чтения Room state.

---

# 21. Database open configuration

Conceptual configuration:

```kotlin
Room.databaseBuilder<AutPlayDatabase>(
    context = applicationContext,
    name = applicationContext.getDatabasePath("autplay.db").absolutePath,
)
    .setDriver(BundledSQLiteDriver())
    .setQueryCoroutineContext(Dispatchers.IO)
    .addMigrations(/* every supported path */)
    .build()
```

Required:

- WAL;
- foreign keys enabled;
- no `fallbackToDestructiveMigration`;
- no main-thread queries;
- query logging only in debug with redaction;
- explicit close only in tests/process lifecycle scenarios;
- database singleton per process.

---

# 22. Schema export and migration policy

```kotlin
plugins {
    id("androidx.room3") version roomVersion
}

room3 {
    schemaDirectory("$projectDir/schemas")
}
```

Generated JSON schemas commit в git.

Rules:

1. Version increment при любом persistent schema change.
2. AutoMigration допускается только для простой additive change после review generated SQL.
3. Rename/delete/type transform - manual Migration.
4. FTS/content change включает rebuild step.
5. Large backfill выполняется bounded chunks или post-migration resumable job, если startup migration слишком долгая.
6. Destructive fallback запрещен.
7. Миграция сохраняет local IDs, journal IDs, playlist order и tombstones.

---

# 23. Migration tests

Использовать Room `MigrationTestHelper` и exported schemas.

Каждая supported path проверяет:

- schema validation;
- foreign keys;
- user data preservation;
- unknown enum/string preservation;
- duplicate playlist entries;
- position order;
- Offline Journal event/hash/sequence;
- pending/dirty edit;
- tombstone retention;
- content URI string preservation;
- FTS rebuild/search;
- recommendation payload bytes/hash;
- open after process restart.

Test chain:

```text
v1 -> v2
v2 -> v3
v1 -> v2 -> v3
latest fresh install
latest restored fixture
```

---

# 24. Property and failure tests

## 24.1. Sync

- duplicate ACK;
- duplicate pull event;
- reordered batch;
- process death after domain write but before commit;
- process death after commit but before network ACK;
- expired local journal lease;
- server conflict with pending local edit;
- new unknown event/enum value;
- reset/bootstrap with non-empty local library.

## 24.2. Playlist

- 10 000 random inserts/reorders/deletes;
- duplicate Track entries preserved;
- concurrent remote/local reorder;
- lexicographic key growth triggers bounded rebalance;
- tombstoned entries do not occupy active position uniqueness.

## 24.3. Audio

- revoked SAF permission;
- missing MediaStore URI;
- same SHA at two URI;
- corrupt/truncated audio;
- storage full during download;
- download complete callback duplicated;
- proactive cache eviction does not delete PINNED.

## 24.4. Search

- Cyrillic/Latin transliteration;
- punctuation/diacritics;
- quotes/operators in user input;
- very long query;
- FTS corruption/rebuild;
- deleted/tombstoned Track excluded.

---

# 25. Performance targets

Reference device tiers:

```text
primary: Samsung A55 class device
minimum: API 26 emulator / low-mid hardware profile
large fixture: 100 000 RecordingProjection, 1 000 playlists,
               1 000 000 ListeningEvent, 100 000 search rows
```

Targets after warm-up:

| Operation | Target |
| --- | ---: |
| Library first page | p95 <= 100 ms |
| FTS top 50 | p95 <= 150 ms |
| Playlist 1 000 entries load | p95 <= 150 ms |
| Journal next batch 100 | p95 <= 50 ms |
| Single local command transaction | p95 <= 50 ms |
| Queue restore | p95 <= 100 ms |
| Cold DB open + validation | measured release gate, no UI ANR |

Performance test records database size, index size, WAL growth and memory.

---

# 26. Retention and compaction

| Data | Policy v1 |
| --- | --- |
| ACKED journal events | Compact after server cursor + safety window |
| Tombstones | Delete only after server ACK and retention deadline |
| Listening events | Configurable; synced old events may compact after profile backup policy |
| Old queue snapshots | Keep latest active + bounded recent |
| Recommendation packs | Remove expired and superseded |
| Search index | Rebuildable |
| Missing transient cache audio | Remove state after reconciliation grace period |
| PINNED/user downloads | Never auto-delete without explicit policy |

Compaction выполняется background job с batch limit и battery/storage constraints.

---

# 27. Backup and restore

`autplay.db` не является portable backup сам по себе:

- content URI может стать недействительным на другом устройстве;
- Media3 DownloadIndex и files должны согласовываться;
- server cursor/device identity нельзя слепо переносить;
- encrypted credentials завязаны на device Keystore.

Portable restore использует Profile Export/Sync bootstrap:

```text
library
playlists and duplicate order
preferences
unresolved UserTrackRef
selected history/settings according to policy
no tokens
no content URI as trusted path
```

После restore выполняется media rescan и fingerprint reconciliation.

---

# 28. Security and privacy

- Tokens/refresh credentials - Keystore-backed storage, не Room.
- Private source URLs - не Room logs/export.
- Journal/audit snapshots проходят redaction.
- SQL user input всегда bound.
- FTS query parser имеет allowlist/limits.
- Database file доступен только application sandbox.
- Debug query logging отключен в release.
- Android backup rules не копируют raw database без отдельного restore design.
- Delete-account workflow очищает user data, caches и cryptographic material по policy.

SQLCipher не является обязательным v1. Его добавление требует threat model, performance test, key recovery policy и migration test; сама по себе библиотека шифрования не заменяет Keystore и redaction.

---

# 29. Deliberate omissions

Не входят в Room Schema v1:

- полный server catalog;
- local vector embeddings;
- Wave room protocol/state;
- collaborative playlist membership;
- lyrics full-text index;
- social sharing cache;
- Web client database;
- local LLM state;
- VPN data;
- raw provider secrets;
- parallel duplicate download progress table.

---

# 30. Acceptance checklist

Room Schema v1 готова к Kotlin entities, когда:

1. Room 3 compatibility gate пройден на physical и minSdk devices.
2. Bound local command меняет domain state и Journal одной transaction; standalone command меняет
   domain state и local mutation outbox одной transaction.
3. Explicit outbox materialization создаёт новый immutable Journal event атомарно и идемпотентно.
4. Local ID не меняется после server sync.
5. Server projection не затирает pending local edit.
6. Playlist duplicate entries и order сохраняются.
7. Media3 остается owner download execution/progress.
8. Content URI не используется как server storage key.
9. FTS5 derived index полностью rebuildable.
10. Unknown enum/event/outbox payload не уничтожает row.
11. Destructive migration fallback отсутствует.
12. Exported schemas и MigrationTestHelper включены в CI.
13. Large fixture выполняет performance targets без ANR.
14. Restore не доверяет старым content URI/device credentials.
15. Room/SQLite failure не может повредить уже существующий audio file.

---

# 31. Следующая реализация

После утверждения schema требуется создать:

1. Kotlin persistence entities и `AutPlayDatabase` version 1.
2. Type-safe ID/value objects и converters.
3. DAO interfaces и repository transaction use cases.
4. Exported schema snapshot.
5. Migration test harness.
6. Large fixture generator.
7. Offline Journal property tests.
8. FTS safe query builder and rebuild command.
9. Media3 DownloadIndex reconciliation adapter.
10. Sync Protocol v1, использующий эти local entities.

---

# 32. P08 executable Room v2 and Media3 ownership addendum

P08 keeps the 26-table Room model and applies the named additive `MIGRATION_1_2`; destructive fallback remains forbidden. Queue snapshots now persist bounded listening context, logical-session checkpoint state, and the user/device/server-profile binding captured when the session starts. Queue entries persist canonical attribution and stable queue-entry identity, so duplicate tracks remain distinct. Listening events persist start/end positions and canonical attribution. Download intents add profile/access context, but never duplicate byte progress.

The executable ownership boundary is:

- Room stores user intent, coarse download state, queue order, repeat/shuffle state and durable logical-listening checkpoints;
- Media3 `DownloadService`/`DownloadManager`/`DownloadIndex` own execution, byte progress and cached media representations;
- a completed Media3 download is resolved through its cache/index representation, never materialized as a fake `content://` URI;
- the bounded download cache and LRU stream cache are separate; automatic eviction may remove stream/proactive cache only, never pinned or user-requested downloads;
- local readability is checked first, then an authorized Vault reference is obtained and credentials are attached only when the data source opens;
- queue/session replacement and recovery finalize stale logical sessions against their captured owner and attribution exactly once.

The exported Room schemas and named migration tests under `apps/android/schemas` and
`apps/android/src/androidTest` are the executable compatibility evidence.

---

# 33. P09 executable Room v7 sync and profile-ownership addendum

P09 advances the executable database through named, non-destructive migrations `MIGRATION_2_3`
through `MIGRATION_6_7`; v1 and v2 remain supported upgrade sources. The v7 export has 29 entities.
The migrations add sync runtime status, independent bootstrap progress, profile-scoped
conflict/tombstone state, append-only recommendation interaction facts and explicit profile
ownership for every synchronized domain projection. Existing rows and immutable pending Journal
events are preserved; no destructive fallback exists.

The executable sync boundary is:

- `sync_cursor` retains the installed opaque cursor, while `sync_bootstrap_state` owns snapshot/page
  progress until an atomic final cutover;
- WorkManager input contains only stable device/profile IDs; event bytes, credentials, cursors and
  URLs remain in Room or protected runtime storage;
- ACK and page preflight completes before mutation, then one Room transaction applies all supported
  projections and advances the applicable checkpoint exactly once;
- unknown, malformed, reordered or incomplete pages do not advance the cursor;
- dirty local rows are never overwritten; deterministic profile-scoped conflict evidence retains
  both sides;
- tombstones may exist without a live local aggregate, and recording redirects update a mapping
  rather than adopting a live Recording projection;
- `server_profile_id` scopes server-ID lookup, synchronized list/search queries, conflicts,
  tombstones, interaction facts and status. Standalone rows use the explicit `legacy-unscoped`
  owner until an authenticated materialization transaction claims them;
- a coordinator attempt drains at most ten pages and requests WorkManager retry when more pages
  remain.

The exported Room schemas, intermediate migration definitions, and API 26 tests under
`apps/android` are the executable preservation evidence.

---

# 34. P13 executable Room v10 Wave recovery addendum

P13 applies named additive `MIGRATION_9_10`. It adds `wave_room`, `wave_preflight` and
`wave_queue_projection` as profile/user/device-bound recovery projections. They cache room epoch,
contiguous command sequence, canonical Recording queue and the current device's availability only.
They never store bearer tokens, room codes, URLs, raw paths, clock samples or Media3 byte progress.

REST snapshot replacement and contiguous WebSocket event application are single Room transactions;
the stored sequence advances only with the complete projection. A gap, epoch mismatch, malformed
event, auth failure or process restart stops Wave execution until a fresh authenticated snapshot and
clock calibration. P08 Media3 remains the sole playback/download owner, and Wave failures never
rewrite ordinary library rows. The v10 Room identity hash is
`eff029c0b73e3189b9ab8e31b0261541` and the exported-file SHA-256 is
`9f42becf68b2bd5a92a1bf788dbc3cda361894db3690d1fa9a77f6cd34aa7c90`; the API 26 migration tests
under `apps/android/src/androidTest` verify v9→v10 preservation.

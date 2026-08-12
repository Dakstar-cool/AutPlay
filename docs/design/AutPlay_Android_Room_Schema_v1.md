# AutPlay Android Room Schema v1

**Статус:** Draft for Android implementation review  
**Версия:** 1.0  
**Database:** `autplay.db`, schema version 1  
**Preferred runtime:** Room 3.0.1, `androidx.room3`, KSP, Kotlin codegen  
**SQLite:** `BundledSQLiteDriver`, WAL  
**Основание:** `AutPlay System Architecture v1`, `AutPlay ER Model v1`, `AutPlay Track Identity v1`  

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
| `device_sequence` | INTEGER | Unique, monotonically increasing |
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

- unique `device_sequence`;
- `(state, next_attempt_at_ms, device_sequence)`;
- `(aggregate_type, aggregate_local_id, device_sequence)`.

## 13.2. Sequence allocation

Device sequence выделяется в той же Room write transaction, что domain change и journal insert.

`device_sequence_counter` singleton:

```text
counter_key TEXT PRIMARY KEY = 'offline_journal'
next_sequence INTEGER NOT NULL
```

Repository сначала атомарно increment/read sequence, затем пишет domain row и event.

## 13.3. Event state recovery

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
| `device_id` | TEXT NOT NULL |
| `last_pulled_server_sequence` | INTEGER NOT NULL |
| `last_acked_device_sequence` | INTEGER NOT NULL |
| `bootstrap_snapshot_id` | TEXT NULL |
| `bootstrap_state` | TEXT NOT NULL |
| `last_sync_at_ms` | INTEGER NULL |
| `updated_at_ms` | INTEGER NOT NULL |

Credentials и base URL хранятся не здесь: token/private material находится в Android Keystore-backed storage; non-secret server settings - в DataStore.

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
| `catalog_snapshot` | INTEGER NOT NULL |
| `model_bundle_version` | TEXT NOT NULL |
| `payload_version` | INTEGER NOT NULL |
| `payload_encoding` | TEXT NOT NULL |
| `payload` | BLOB NOT NULL |
| `payload_sha256` | BLOB NOT NULL, 32 bytes |
| `created_at_ms` | INTEGER NOT NULL |
| `expires_at_ms` | INTEGER NOT NULL |

Pack является candidate data, а не authorization на stream. Expired pack MAY использоваться только для offline local recommendations по explicit fallback policy.

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
2. Every local command меняет domain state и Journal одной transaction.
3. Local ID не меняется после server sync.
4. Server projection не затирает pending local edit.
5. Playlist duplicate entries и order сохраняются.
6. Media3 остается owner download execution/progress.
7. Content URI не используется как server storage key.
8. FTS5 derived index полностью rebuildable.
9. Unknown enum/event payload не уничтожает row.
10. Destructive migration fallback отсутствует.
11. Exported schemas и MigrationTestHelper включены в CI.
12. Large fixture выполняет performance targets без ANR.
13. Restore не доверяет старым content URI/device credentials.
14. Room/SQLite failure не может повредить уже существующий audio file.

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

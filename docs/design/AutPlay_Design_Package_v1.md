# AutPlay Design Package v1

**Статус:** Ready for implementation review  
**Версия:** 1.0  
**Дата фиксации:** 2026-08-12  
**Основание:** AutPlay Draft 0.3  

---

# 1. Результат

Пакет закрывает design baseline перед созданием persistence и sync code:

- system boundaries и deployment modes;
- conceptual ER model;
- Track identity и fingerprint decision policy;
- physical PostgreSQL schema;
- Android Room schema;
- migration и verification gates.

Пакет не означает, что эмпирические thresholds identity matcher или ML model уже выбраны. Такие значения фиксируются только после benchmark.

---

# 2. Состав пакета

| Артефакт | Роль | Статус |
| --- | --- | --- |
| [ТЗ AutPlay](<ТЗ AutPlay.md>) | Product, functional и non-functional requirements | Draft 0.3 baseline |
| [System Architecture](<AutPlay System Architecture v1.md>) | Modules, ownership, deployment, failure boundaries | v1 |
| [ER Model](<AutPlay ER Model v1.md>) | Conceptual entities, relations и lifecycle | v1 |
| [Track Identity](<AutPlay_Track_Identity_v1.md>) | Candidate generation, evidence, scoring, calibration, merge safety | v1 |
| [PostgreSQL decisions](<AutPlay_PostgreSQL_Schema_v1.md>) | Physical decisions, Alembic plan и test matrix | v1 |
| [PostgreSQL DDL](<AutPlay_PostgreSQL_Schema_v1.sql>) | Executable reference schema | v1 |
| [Android Room Schema](<AutPlay_Android_Room_Schema_v1.md>) | Local-first Android persistence contract | v1 |
| [Codex Goal: Schema Foundation](<AutPlay_Codex_Goal_Schema_Foundation_v1.md>) | Bounded implementation prompt | v1 |

---

# 3. Нормативный приоритет

При противоречии применяется следующий порядок:

1. Security, privacy и destructive-data constraints из ТЗ.
2. Более узкая спецификация для своей области.
3. Physical schema для persistence details.
4. Architecture для dependency direction и ownership.
5. ER model для conceptual meaning.

Пример: смысл `Recording` задает ER model, решение о match задает Track Identity, PostgreSQL columns/constraints задает DDL, а Android projection задает Room Schema.

Содержательное противоречие не исправляется молча. Оно оформляется ADR или change set и синхронно отражается в затронутых документах.

---

# 4. Зафиксированные решения

## 4.1. Product and topology

- Android standalone mode остается полноценным local-first product mode.
- Personal server является optional enhancement для Vault, sync, import и server recommendations.
- Production server - Linux x86_64; CPU-only server core остается cross-platform.
- RTX 3060 используется только изолированным optional ML worker.
- VPN не входит в AutPlay и остается внешней инфраструктурой.

## 4.2. Identity

- `VaultObject`, `AudioVariant`, `Recording`, `ReleaseTrack` и `UserTrackRef` не взаимозаменяемы.
- SHA-256 доказывает равенство bytes, а не музыкальной Recording.
- Fingerprint является versioned evidence, а не primary key.
- ISRC и provider ID не считаются безусловно уникальной Recording identity.
- False merge опаснее unresolved/missed match.
- Auto-match открывается только после benchmark и calibration gate.

## 4.3. Server persistence

- PostgreSQL 18.x и pgvector 0.8 compatible range.
- UUIDv7 для server-created IDs.
- Text states с named CHECK вместо изменяемых database enums.
- PostgreSQL job queue использует lease, heartbeat и `FOR UPDATE SKIP LOCKED`.
- Exact vector search является initial baseline; HNSW добавляется только после model/dimension benchmark.
- Vault bytes не хранятся в PostgreSQL.

## 4.4. Android persistence

- Room 3.0.1 является preferred greenfield choice после compatibility gate.
- `BundledSQLiteDriver`, coroutine APIs, KSP и FTS5.
- Local aggregate ID никогда не заменяется server ID.
- Domain mutation и Offline Journal event фиксируются одной Room write transaction.
- Media3 DownloadIndex владеет execution/progress, Room хранит user intent и reconciliation state.
- FTS является derived и полностью rebuildable.
- Destructive migration fallback запрещен.

---

# 5. Server/local mapping

| Concept | PostgreSQL source of truth | Android state |
| --- | --- | --- |
| Recording | `catalog.recording` | `recording_projection` |
| Release position | `catalog.release_track` | `release_track_projection` |
| User intent/ref | `library.user_track_ref` | `user_track_ref` |
| Library membership | `library.library_entry` | `library_entry` |
| Playlist | `playlist.playlist` | `playlist` |
| Playlist item/order | `playlist.playlist_entry` | `playlist_entry` |
| Listening event | `library.listening_event` | `listening_event` |
| Device command dedupe | `sync.device_event_inbox` | `offline_journal_event` |
| Server change stream | `sync.sync_event` | applied projection + `sync_cursor` |
| Tombstone | `sync.tombstone` | `tombstone` |
| Offline recommendations | `ml.offline_recommendation_pack` | `recommendation_pack` |
| Audio bytes | Vault backend + `vault.vault_object` metadata | MediaStore/SAF/Media3 + `local_audio_state` |

PostgreSQL и Room не являются зеркальными schemas. Android хранит bounded projection и pending local intent, server хранит canonical shared state.

---

# 6. Gates перед feature implementation

| Gate | Доказательство | Статус |
| --- | --- | --- |
| PostgreSQL syntax/inventory | Reference DDL structurally parses, object counts checked | Passed for design artifact |
| PostgreSQL executable migration | Clean upgrade/downgrade and constraint tests against pinned PostgreSQL image | Pending implementation |
| Room ecosystem compatibility | Build, R8, physical device, minSdk emulator, FTS5, process restart | Pending spike |
| Room migration harness | Exported schema and `MigrationTestHelper` fixtures | Pending implementation |
| Identity benchmark | Labeled positive/hard-negative set, calibration, shadow report | Pending dataset |
| Offline Journal failure safety | Property tests with crash/retry/reorder/duplicate scenarios | Pending implementation |
| Large fixture performance | Server and Samsung A55/minimum tier measurements | Pending implementation |

Design status `Passed` не заменяет execution test against real PostgreSQL/Android runtime.

---

# 7. Рекомендуемый порядок реализации

1. Repository skeleton и pinned toolchains.
2. PostgreSQL initial Alembic migration from reference DDL.
3. Typed SQLAlchemy persistence mappings without feature logic.
4. Room 3 compatibility spike.
5. Room entities, DAOs, exported schema v1 и migration harness.
6. Offline Journal transactional command skeleton.
7. Sync Protocol v1: push, ACK, pull, cursor, tombstone, bootstrap, conflict.
8. Track identity benchmark harness before auto-match.
9. Vault ingest happy path with CPU-only workers.
10. Feature slices Library -> Playlist -> Playback -> Import -> Wave -> ML.

Каждый шаг должен быть independently testable. GPU, HNSW и external provider integrations не блокируют первые persistence/sync slices.

---

# 8. Следующий design artifact

Следующий нормативный документ - `AutPlay Sync Protocol v1`.

Он должен определить:

- device registration и profile binding;
- push batch envelope и idempotency;
- ACK semantics;
- server event ordering и cursor contract;
- tombstone propagation/retention;
- bootstrap/reset;
- conflict taxonomy и resolution;
- payload/version compatibility;
- retry, lease и failure matrix;
- security limits и observability;
- conformance test vectors для server и Android.

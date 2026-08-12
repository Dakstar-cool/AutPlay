# P05 - Android Local-First Foundation

Выполни только phase P05. Следуй common protocol и прочитай `HANDOFF_P04.md`.

## Цель

Создать Android application foundation с Room schema v1, transactional repository skeleton и Offline Journal, сохранив возможность полноценного запуска без server configuration.

## Inputs

- `docs/design/AutPlay_Android_Room_Schema_v1.md`
- `docs/design/AutPlay ER Model v1.md`
- `docs/design/AutPlay_Sync_Protocol_v1.md`
- Android sections of System Architecture and product specification

## Compatibility gate first

До массовой генерации entities проверь pinned Room 3/Kotlin/KSP/AGP stack:

- compile debug/release;
- `BundledSQLiteDriver` open/restart;
- WAL and foreign keys;
- FTS5 query;
- R8/minification smoke;
- minimum-SDK emulator and Samsung A55 class physical target plan.

Если Room 3 блокирует ecosystem, остановись с evidence. Не переключайся на Room 2 после создания user schema silently.

## Scope

1. Layered Android modules/packages: domain, application/use cases, data, playback seam, UI shell.
2. Stable typed local/server IDs and unknown-safe persisted values.
3. Room v1 entities, FK/indexes, DAOs and exported JSON schema according to specification.
4. FTS5 external-content setup and safe query builder.
5. Transaction runner for domain mutation + sequence allocation + Offline Journal insert.
6. Journal lease/retry state repository without network transport.
7. Sync cursor/tombstone/conflict persistence matching P04.
8. DataStore for non-secret settings and Keystore-backed credential port without real token.
9. WorkManager scheduling seam using stable IDs only.
10. Minimal Compose screen proving offline database flow and process recreation.

## Constraints

- No server required at startup.
- No `fallbackToDestructiveMigration`.
- No raw audio bytes or absolute paths in Room.
- No duplicate download progress state machine.
- No UI direct multi-DAO writes.
- No full sync transport or playback implementation.
- Do not collapse Recording/UserTrackRef/AudioVariant/local URI.

## Required tests

- fresh install/open/restart;
- exported schema validation;
- FTS insert/update/delete/rebuild and hostile query input;
- local command rollback leaves neither domain row nor event;
- committed command always has event/sequence;
- duplicate playlist item allowed and active order unique;
- queue active-slot uniqueness;
- unknown string value preserved;
- revoked/missing URI representation safe;
- expired Journal lease recovery;
- large fixture baseline for key DAOs.

## Acceptance

Android launches offline, a sample local command persists atomically through process recreation, required tests are green, and Room schema exactly reflects approved v1 or an accepted migration ADR.

Create `HANDOFF_P05.md`, update A-005..A-007 and stop.

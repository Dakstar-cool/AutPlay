# P07 - Library, Playlist, History and Search Vertical Slice

Выполни только phase P07. Следуй common protocol и прочитай `HANDOFF_P06.md`.

## Цель

Создать первый user-visible local-first vertical slice: пользователь добавляет локальную композицию/intent в библиотеку, ищет ее, управляет playlist, фиксирует preference/history и сохраняет результат после restart без server.

## Inputs

- Product specification library/import/search/playlist/history sections
- ER user library/catalog/playlist entities
- Room schema and P05 repositories
- PostgreSQL schema and P03 application boundaries
- Sync contract event types

## Scope

1. Pure domain rules and commands for UserTrackRef, LibraryEntry, preference, Playlist/Entry and ListeningEvent.
2. Android MediaStore/SAF scan/import metadata path with URI permission validation.
3. Unresolved UserTrackRef creation without fake global Recording.
4. Transactional Android use cases and Offline Journal events.
5. Library/search/playlist/history Compose flows with observable state and stable errors.
6. FTS content projection and deterministic bounded ranking.
7. Duplicate Track entries and fractional ordering/rebalance behavior.
8. Server application/repository commands needed for later sync, with object authorization, but no duplicate REST write path that bypasses sync rules.
9. Minimal OpenAPI/query endpoints for server projections where required.
10. Import provenance and availability projection.

## Constraints

- UI never writes DAO directly.
- Local action never waits for server.
- Removing from library does not delete audio or Recording.
- History events are append-only logical play events, not every seek.
- Unknown/ambiguous item remains visible and repairable.
- No matching auto-merge, external service adapter or playback engine yet.

## Required tests

- airplane-mode add/remove/restore library;
- duplicate playlist entries and random reorder/delete/rebalance property tests;
- restart/process recreation;
- FTS Cyrillic/Latin/transliteration/punctuation/limits;
- missing/revoked content URI does not delete user intent;
- preference/history exclusion rules;
- cross-user server authorization;
- local command and Journal atomicity for each aggregate;
- large library/playlist query baseline.

## Acceptance

On Android without server, a user can create and reopen a meaningful library and playlists; server-side corresponding commands are authorization-safe and compatible with P04, while sync transport remains deferred.

Create `HANDOFF_P07.md`, update A-008..A-010 and stop.

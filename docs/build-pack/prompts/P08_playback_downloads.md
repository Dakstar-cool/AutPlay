# P08 - Playback, Queue and Offline Downloads

Выполни только phase P08. Следуй common protocol и прочитай `HANDOFF_P07.md`.

## Цель

Реализовать надежное audio playback ядро Android с source selection LOCAL -> Vault direct stream, persistent queue и Media3-managed offline downloads.

## Inputs

- Product playback/download/cache requirements
- System Architecture playback and stream selection flow
- Room queue/local audio/download intent schema
- P06 stream contract
- `REFERENCE_PROJECTS.md` sections AndroidX Media3 and Jellyfin

## Scope

1. Media3 `MediaSessionService`/player lifecycle and notification/control integration.
2. Stable MediaItem mapping to UserTrackRef and selected AudioSource.
3. Source resolver:
   - validate local content URI;
   - select local if readable;
   - otherwise request authorized Vault variant/stream;
   - return stable unavailable reason.
4. Queue snapshot/entries, current position, shuffle/repeat and restore.
5. Preflight current/next item and bounded prefetch.
6. Media3 `DownloadService`, singleton DownloadManager/DownloadIndex and cache policy.
7. Room `download_intent` reconciliation without duplicate progress truth.
8. PINNED/USER_DOWNLOAD/PROACTIVE_CACHE eviction policy.
9. ListeningEvent finalization with recommendation/context IDs where present.
10. Compose mini/full player and download state UI sufficient for behavior verification.

## Constraints

- Do not transcode on Android unless Media3 decoder path requires normal decode.
- Do not trust stale URI or cached server URL.
- Download cache and stream cache have separate retention semantics.
- Proactive cache never evicts pinned/user download.
- Playback must continue locally when API/GPU/provider is down.
- No Wave synchronization yet.

## Required tests

- local preferred over server;
- revoked/missing URI falls back without losing library row;
- Range/network interruption and resume/retry behavior;
- process death queue/position restore;
- duplicate DownloadManager callback/reconciliation;
- storage full and eviction ordering;
- download survives app background according to Android requirements;
- auth expiry refresh path without leaking URL/token;
- repeated track in queue/playlist;
- listening event emitted once per logical session.

## Acceptance

Reference device/emulator can play local and Vault audio, restore queue after process death and manage offline download with Media3 as sole execution truth.

Create `HANDOFF_P08.md`, update A-014..A-017 and stop.

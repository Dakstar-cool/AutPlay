# Android UI functional parity evidence — 2026-09-10

This document records implementation and verification evidence for
[`TASK_ANDROID_UI_FUNCTIONAL_PARITY.md`](../design/TASK_ANDROID_UI_FUNCTIONAL_PARITY.md).
The work is post-v0.3.0 development-branch functionality and is not part of the already published
v0.3.0 assets.

## Functional matrix

| Surface | End-to-end ownership and evidence |
| --- | --- |
| Vault Search | Bounded typed remote rows retain stable identity, metadata, source and availability. Query/profile/binding generations reject stale replies. Resolvable rows create a durable `SEARCH` queue; unavailable rows remain visibly disabled. Compose coverage exercises populated, empty, error and click states. |
| Taste exclusion | Listen- and queue-session-level choices are owned by the durable playback snapshot, remain independent from Like/Dislike and are copied to the final listening event and Journal fact. Repository/core, restart/recreation and finalization tests cover the path. |
| Local import | The UI derives pause/resume/cancel actions from the job state, confirms cancellation, enforces single-flight and reloads the authoritative Room state through `LocalImportReviewRepository`. Policy and Compose tests cover valid and forbidden transitions. |
| Wave host transfer | The server publishes at most seven active non-secret targets and revalidates membership, freshness and revocation at mutation time. Android confirms the named target and accepts role changes only from a refreshed authoritative snapshot. Contract, PostgreSQL, coordinator and Compose tests cover success, stale target, member visibility and rejection. |
| Listening history | Keyset-paginated Room projection orders by `(started_at_ms, listening_event_id)` descending, preserves duplicate listens and exposes play/open actions through the durable queue boundary. Repository and Compose tests cover empty, populated, duplicate and next-page states. |
| Downloads | A bounded presentation lists requested, queued, downloading, paused, completed, failed and cancelled intents without private URLs. Play, retry and cancel remain application-owned; Media3 remains the progress authority. Policy and Compose tests cover the complete state/action matrix. |
| Server Features | Search and recommendation payloads are rendered as bounded meaningful rows with explicit loading, empty and error states. Replay labels distinguish exact and algorithmic refreshes, and binding guards prevent stale persistence. Compose tests cover populated, empty, error and replay paths. |

## Automated gates

The following commands were run from the repository root on Windows:

```powershell
.\gradlew.bat --no-daemon --console=plain --max-workers=1 `
  :apps:android:lintDebug `
  :apps:android:testDebugUnitTest `
  :apps:android:assembleDebug

.\gradlew.bat --no-daemon --console=plain --max-workers=1 `
  :apps:android:compileDebugAndroidTestKotlin

powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\check.ps1 -ServerOnly
```

Results:

- Android lint, JVM unit tests and debug APK assembly: **PASS**.
- Android instrumentation source compilation: **PASS**.
- Server/contract/PostgreSQL gate: **PASS** — 168 contract/release tests and 879 PostgreSQL tests;
  one documented Windows symlink test skipped.
- Functional-parity connected subset on headless `codex_p13_api26`, Android API 26:
  **46/46 PASS**. The subset includes Room 14→15 migration, playback restart/finalization,
  Wave coordinator/Compose, import, adaptive shell, Vault/Server Features, History and Downloads.
- Complete connected suite on the same AVD: **PASS** — 192 tests, zero failures and three expected
  skips for separately orchestrated process-stage/external pairing scenarios.

Two Media3 failures found during the initial complete-suite run were closed before the final pass:

- the manifest now declares Media3 `PlatformSchedulerService` with its required JobService
  permission and `RECEIVE_BOOT_COMPLETED` capability;
- the deterministic download fixture confines `DownloadManager` access to its application looper,
  removes the irrelevant emulator-network requirement, resets Media3's process-static service
  helper between service instances and waits for the exact download identity before asserting Room
  reconciliation.

## Manual layout and unavailable-state check

The installed debug APK was inspected on the API 26 AVD after a clean onboarding flow:

- compact portrait, light theme: Search remained usable with the local scope and no configured
  server;
- expanded landscape, dark theme: the same Search destination and input surface were preserved,
  navigation adapted to the expanded rail, and no remote action was offered without a real server
  capability;
- the no-binding/offline condition was honest: local Search remained available while Vault/server
  controls were absent rather than reporting a successful remote operation.

The deterministic Compose suite additionally covers compact/medium/expanded destination retention
and the explicit offline/error presentations for the parity surfaces.

## Durable schema and decisions

- Room schema version 15 is exported at
  `apps/android/schemas/app.autplay.data.local.AutPlayDatabase/15.json` and pinned by
  `RoomSchemaExportTest`.
- [`AutPlay_Android_Room_Schema_v1.md`](../design/AutPlay_Android_Room_Schema_v1.md) records the
  migration and normalized schema hash.
- [`AutPlay_Wave_Protocol_v1.md`](../design/AutPlay_Wave_Protocol_v1.md) records bounded host-transfer
  targets and authoritative refresh semantics.
- [`ADR-050`](../adr/ADR-050-android-ui-functional-parity-state-ownership.md) records state ownership,
  pagination, capability and no-optimistic-success decisions.

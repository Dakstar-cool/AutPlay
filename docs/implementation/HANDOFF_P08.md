# HANDOFF P08 — Playback, Queue and Offline Downloads

## Outcome

P08 is PASS. Android now owns playback through Media3 `MediaSessionService`/ExoPlayer, restores a
durable duplicate-preserving queue and logical listening session, selects readable local audio
before a fresh authorized Vault source, and manages offline downloads through Media3
`DownloadService`/`DownloadManager`/`DownloadIndex`. Room remains the intent/coarse-state store and
does not duplicate byte progress. A-014 through A-017 are PASS. P09 is eligible but has not started.

The final independent re-review reports no remaining Critical or Major findings.

## Prerequisite evidence

- `HANDOFF_P07.md` was present and green: A-008 through A-010 PASS, canonical repository checks,
  23 host tests and 30 API 26 tests, with no blocker for P08.
- The P08 prompt, common protocol, decision register, plan/risk/version records, Room schema,
  playback/Vault/product inputs and Media3 references were reviewed before implementation.
- P08 does not alter P04 sync contracts or start the P09 transport/projection engines.

## Delivered scope

### Playback, queue and source selection

- Application-scoped Room/runtime ownership; Activity recreation no longer closes the database.
- `AutPlayPlaybackService` owns ExoPlayer and `MediaSession`; notification, lock-screen, headset and
  background transport controls use the Media3 session boundary.
- Exported MediaSession admission accepts this app and trusted system/media controllers only;
  untrusted third-party controllers receive no session.
- Queue-entry UUID is the `MediaItem.mediaId`, preserving repeated tracks. Current entry, bounded
  position, deterministic shuffle seed, repeat mode and stable source state are restored.
- Checkpoints occur every 15 seconds and on pause, seek completion, mode changes, errors and orderly
  teardown. Seeks update position but do not manufacture played time.
- Readable MediaStore/SAF content is preferred; completed Media3 download cache and then fresh Vault
  authorization are fallback sources. Current and next entries are preflighted. A missing/revoked
  URI retains the library row and exposes a stable unavailable/fallback reason.
- Vault uses a non-secret stable synthetic URI. Bearer credentials are attached at data-source
  `open`, a 401 performs one single-flight refresh/retry, and Room/logs contain no token or private
  URL.

### Listening sessions and Room v2

- Named additive `MIGRATION_1_2`; the 26-table model is retained and destructive fallback remains
  forbidden.
- Queue snapshots persist bounded listening context, logical-session checkpoint state and the
  user/device/server-profile owner captured at session start. Queue entries/listening events retain
  canonical immutable recommendation attribution, including local/server impression IDs.
- One stable listening-event ID is finalized at most once into the P07 transactional listening plus
  Journal/outbox path. Profile changes cannot retarget a running session.
- Queue replacement finalizes the old logical session. Startup also recovers stale sessions from
  inactive snapshots, closing the process-kill window between queue activation and service callback
  while retaining the original owner, context and event identity.
- Exported schema hashes: v1
  `f063c8ec14ecf8c1fbd7d926f5e9322021e1187c2bf6c486c6b9a6aed88924d2`; v2
  `c69acd49acceadf9c1c92874ab2eca9069c6958f1bd4c313136ed8a5e80d3acf`.

### Downloads and storage policy

- Process-singleton Media3 `DownloadManager`/`DownloadIndex` and real `AutPlayDownloadService` with
  `PlatformScheduler`; Media3 alone owns execution and byte progress.
- Room stores stable download intent, storage/source/quality policy, profile, coarse state and stable
  failure code. Startup/listener reconciliation is idempotent; duplicate intent/callback paths do
  not create a second progress truth.
- A separate `NoOpCacheEvictor` download cache and bounded 128 MiB LRU stream cache preserve
  different retention semantics. The player reads download cache, then stream cache, then the
  authorized upstream.
- Quota/free-space admission is deterministic. Stream/proactive cache is evictable in stable LRU/
  age order; `USER_DOWNLOAD` and `PINNED` content are never automatically evicted. Insufficient
  protected space returns `STORAGE_FULL`.
- Stable failure mapping covers auth, missing/unauthorized Vault content, transient server/network,
  parser/format and storage-full failures.
- Terminal Room reconciliation uses a process-scoped coroutine so Media3 stopping a completed
  service instance cannot cancel its terminal write; process restart remains covered by startup
  reconciliation.

### UI

- Compose mini/full player state reflects the actual service title, source, unavailable reason,
  position, play state, shuffle and repeat mode and exposes play/pause/resume/stop/seek controls.
- Download intent/state is observable without making Compose an execution or progress owner.

## Main changed paths

- Media3/runtime:
  - `apps/android/src/main/kotlin/app/autplay/playback/`
  - `apps/android/src/main/kotlin/app/autplay/download/`
  - `apps/android/src/main/kotlin/app/autplay/AutPlayRuntime.kt`
  - `apps/android/src/main/kotlin/app/autplay/MainActivity.kt`
  - `apps/android/src/main/AndroidManifest.xml`
- Application/persistence:
  - `apps/android/src/main/kotlin/app/autplay/application/playback/PlaybackPersistenceRepository.kt`
  - `apps/android/src/main/kotlin/app/autplay/application/download/DownloadIntentRepository.kt`
  - `apps/android/src/main/kotlin/app/autplay/data/local/AutPlayDatabase.kt`
  - `apps/android/src/main/kotlin/app/autplay/data/local/entity/Entities.kt`
  - `apps/android/src/main/kotlin/app/autplay/data/local/dao/Daos.kt`
  - `apps/android/schemas/app.autplay.data.local.AutPlayDatabase/2.json`
- Dependency/decision records:
  - `gradle/libs.versions.toml`
  - `apps/android/build.gradle.kts`
  - `docs/adr/ADR-021-p08-media3-room-playback-download-ownership.md`
- Evidence:
  - `apps/android/src/test/java/app/autplay/playback/PlaybackCoreTest.kt`
  - `apps/android/src/test/java/app/autplay/download/DownloadPoliciesTest.kt`
  - `apps/android/src/androidTest/kotlin/app/autplay/playback/`
  - `apps/android/src/androidTest/kotlin/app/autplay/download/Media3DownloadRuntimeTest.kt`
  - `apps/android/src/androidTest/kotlin/app/autplay/application/playback/PlaybackPersistenceRepositoryTest.kt`
  - `apps/android/src/androidTest/kotlin/app/autplay/data/local/P08RoomMigrationTest.kt`

## Decisions, migration and contracts

- ADR-021 accepts Media3 `1.10.1`, Room v2 captured-session ownership, no fake `content://` URI for
  cached downloads, separate stream/download caches and restricted exported-session admission.
- `MIGRATION_1_2` is additive and initializes new nullable/default fields without deleting v1 data.
- Stable synthetic Vault/download identifiers persist; resolved URL/header/credential values do not.
- WorkManager remains the P05 deferred stable-ID seam. It does not download media or duplicate the
  Media3 scheduler/index.
- No server/Alembic migration, public API route, sync schema, P09 engine or future-phase dependency
  was added.

## Exact verification evidence

### Android host, release and repository gate

With `JAVA_HOME` set to the pinned Microsoft OpenJDK `17.0.20+8-LTS`:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\check.ps1
```

PASS:

- root harness: `80 passed`;
- device-independent P04 contracts: `51 passed`;
- Android: lint, `35` host tests, debug APK and minified release/R8 APK; Gradle
  `BUILD SUCCESSFUL in 2m 46s`;
- server/real PostgreSQL: `346 passed, 1 skipped in 147.80s`; the skip is the already documented
  Windows symlink-privilege case, not a product-path skip;
- PostgreSQL `18.4` and pgvector `0.8.6` verified; scoped container, network and volume removed.

The first invocation without `JAVA_HOME` failed closed before any product gate with
`JAVA_HOME must point to the pinned JDK 17`; the recorded PASS above uses the required pinned JDK.

### API 26 connected gate

```powershell
.\gradlew.bat "-Dorg.gradle.java.home=$env:JAVA_HOME" --no-daemon --console=plain `
  :apps:android:connectedDebugAndroidTest
```

PASS on disposable `autplay_p08_api26` (Android 8.0/API 26): `44/44`, zero skipped/failures,
`BUILD SUCCESSFUL in 1m 1s`.

The suite proves real local and Vault ExoPlayer READY, local-first/revoked-URI behavior, one-refresh
auth retry, Room v1→v2 migration, duplicate queue identity, immutable attribution/hash/finalize-once,
profile switch, queue-replacement crash recovery, controller admission, interrupted Range download,
DownloadService command restart, Room reconciliation, singleton cache/index ownership and UI restart.

### Explicit process-death queue proof

1. Targeted `stage1_seedServiceAndWaitForPeriodicCheckpoint`: PASS in `57s`; waits for the real
   15-second service checkpoint and records position >=10 seconds.
2. `adb -s emulator-5580 shell am force-stop app.autplay`; subsequent `pidof app.autplay` was absent.
3. Targeted `stage2_verifyServiceRestoresPersistedQueueAfterFreshConnection`: PASS in `43s` and
   verifies the same queue entry and durable position after a fresh service connection.

After device evidence, `adb emu kill` stopped the emulator and the SDK `avdmanager delete avd -n
autplay_p08_api26` command removed the disposable AVD; `adb devices` was empty.

### Review/fix loop

Independent review initially found eight Major findings: old-session loss on queue replacement,
missing periodic/mode checkpoints, swallowed unavailable reasons, unwired storage policy,
mutable-profile attribution, open exported-session admission, weak service/process acceptance proof,
and static player UI. After fixes, a second review found the queue replacement crash window and a
test that resumed the manager directly. Startup stale-session recovery and the public
`DownloadService.sendResumeDownloads` path plus service-lifetime-safe reconciliation closed them.
The final independent re-review reports zero remaining Critical/Major findings.

## Not delivered by design

- No P09 push/ACK/pull transport, cursor/tombstone/bootstrap/conflict engines or canonical server
  interaction projection.
- No Wave synchronization, collaborative queue, probabilistic matching/merge, external acquisition,
  Android transcoding, recommendation serving/model pipeline, GPU runtime or production deployment.
- No P06 variant-discovery API was invented. Vault fallback uses a previously known authorized
  `server_audio_variant_id` and fails with a stable reason when it is unavailable.
- Samsung A55-class physical-device and production storage/network qualification remain P14 gates.

## Risks and debt

- R-005 playback fallback is mitigated; repair/rescan UX may still expand later.
- R-012 is mitigated on API 26 and minified release/R8; the named physical-device gate remains.
- R-020 Android capture/ownership is mitigated through P08; canonical server projection remains P09.
- Media3 is annotated as an unstable API by AndroidX; its exact pin changes only through the
  compatibility/ADR/full-gate process.

## Next prerequisite and Git state

P09 is eligible but not started. Its exact prompt is
`docs/build-pack/prompts/P09_sync_end_to_end.md`; it must consume the green P08 handoff and the
unchanged P04 vectors.

The worktree already contained the uncommitted P01-P07/harness history when P08 began. P08 changes
are intentionally left uncommitted and unpushed; no unrelated user changes were reset or deleted.

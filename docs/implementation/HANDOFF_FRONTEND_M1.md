# AutPlay Android frontend milestone M1 handoff

## Outcome

`PASS`, qualified on 2026-08-18 by the successor frontend M2 physical-device gate. The original M1
checkpoint completed host-side implementation but left device behavior outstanding; M2 subsequently
ran 89/89 connected tests on a physical Samsung SM-A556E, including the M1 navigation, settings,
playback and lifecycle surfaces. See `HANDOFF_FRONTEND_M2_SERVER_SURFACES.md`. This is not P15 and
does not change the P00-P14 product phase graph or the P14 local RC claim.

## Delivered scope

- Typed Compose destinations for Home, Search, Library, playlists, history, downloads,
  import/review, expanded player controls, Wave, sync, profile, settings and privacy/data surfaces.
- Adaptive shell using existing dependencies: compact `<600dp` bottom navigation and a shared rail
  renderer at `>=600dp`; medium and expanded width classes are retained for later content policy.
- Persistent mini-player and expanded player controls bound to the existing Media3 owner, including
  pause/resume, seek, shuffle, repeat and explicit like/dislike actions.
- Actionable local FTS results that can start a durable search-origin playback queue.
- Real P13 Wave lobby with invite allow-list, create/join/share, host queue/pause/close and member
  leave actions; room codes remain process/UI state and are not persisted.
- System/light/dark appearance, four accent palettes, metered-sync and Wave-prefetch preferences in
  the existing non-secret DataStore adapter.
- Android SAF tree selection plus a bounded 200-audio-document scan into the existing local
  import/review pipeline. Only a revocable `content://` permission is stored; no raw path exists.
- Bounded secret-free settings export/import. Credentials, server URL, user/device binding and the
  device-specific tree URI are excluded.
- Profile actions explicitly report missing contracts instead of claiming password/device success.
- Current-track feedback resolves the active durable queue entry, Wave prefetch consumes the stored
  mode, stale room callbacks are generation-scoped, synchronous Wave transport calls leave the UI
  dispatcher, and DataStore field changes are atomic with stable update errors.

## Not delivered or claimed

- No password-change endpoint, full profile/media export contract, Party Mode, lyrics, statistics,
  live external provider, public-WAN Wave topology or production account-management UI was invented.
- Strict synchronized Wave start uses the existing authenticated `/clock`, `/timing`, preflight and
  `/start` readiness gate plus the mandated seven-sample/20-retained low-RTT clock estimator. An
  aborted gate is shown as waiting; the client never fakes playback.
- The existing P14 feature implementations remain authoritative. Several deep item-detail flows
  still use the established first-item/demo seams and need a later product-UX milestone for complete
  per-item editing, a dedicated queue surface/reordering, a distinct expanded multi-pane renderer,
  and visual polish.
- M1 itself did not install on the user's production-ID app. The successor M2 side-by-side QA build
  later qualified the combined M1/M2 behavior with 89/89 connected tests on the same Samsung device
  without uninstalling or clearing the existing app.

## Changed files

- `apps/android/src/main/kotlin/app/autplay/MainActivity.kt`
- `apps/android/src/main/kotlin/app/autplay/ui/`
- `apps/android/src/main/kotlin/app/autplay/application/settings/SettingsTransferCodec.kt`
- `apps/android/src/main/kotlin/app/autplay/application/importing/ContentTreeAudioScanner.kt`
- `apps/android/src/main/kotlin/app/autplay/application/importing/LocalImportReviewRepository.kt`
- `apps/android/src/main/kotlin/app/autplay/application/library/AddLocalTrack.kt`
- `apps/android/src/main/kotlin/app/autplay/data/settings/NonSecretSettingsStore.kt`
- `apps/android/src/main/kotlin/app/autplay/application/wave/WaveCoordinator.kt`
- `apps/android/src/main/kotlin/app/autplay/playback/PlaybackSessionOwner.kt`
- `apps/android/src/main/kotlin/app/autplay/playback/AutPlayPlaybackService.kt`
- `apps/android/src/main/kotlin/app/autplay/AutPlayRuntime.kt`
- focused host/instrumentation tests under `apps/android/src/test` and `apps/android/src/androidTest`

Pre-existing dirty transport, credential, playback and deployment files were preserved and are not
claimed by this handoff.

## Contracts and migrations

- No PostgreSQL, Room, REST or event-contract migration.
- DataStore gains additive non-secret keys for appearance, accent, SAF tree URI and Wave prefetch.
- Wave adds UI-safe coordinator entry points for contract-backed `QUEUE` and `PAUSE`; the existing
  P13 sequence, authorization and transport contract remains unchanged.

## Commands and results

- `:apps:android:testDebugUnitTest --rerun-tasks` — PASS for the complete host unit suite.
- `:apps:android:lintDebug :apps:android:assembleDebug` — PASS.
- Focused `SettingsTransferCodecTest`, `AdaptiveLayoutTest`, `FrontendBindingTest`,
  `WaveCoreTest` and `OkHttpWaveTransportTest` — PASS.
- `:apps:android:compileDebugAndroidTestKotlin` — PASS, including `AdaptiveShellTest`.
- Successor M2 gate: `:apps:android:connectedDebugAndroidTest` with the side-by-side QA application —
  89 tests, 0 failures, 0 skipped; this closes the original M1 device-evidence gap.

## Risks and next eligible work

- Run compact/medium/expanded screenshot and behavior checks on disposable emulator profiles, then
  complete item-level playlist/queue/track-detail UX.
- Bind server device/session management only after the frontend receives an explicit application
  port; add password change only after an approved server contract exists.
- Exercise multi-device Wave clock/readiness/start on disposable authenticated device profiles.
- Keep raw paths, credentials, private URLs and room codes out of normal UI diagnostics/exports.

## Git state

M1 is committed together with its completed M2 server-surface successor and README assets as one
post-P14 checkpoint. No deployment, publication or production signing is part of that commit.

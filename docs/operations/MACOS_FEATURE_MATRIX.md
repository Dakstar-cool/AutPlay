# macOS stage 0: Android feature inventory

Inventory date: 2026-10-13. Source baseline: `102380418421f87679732406f8052150e8226da8`.
Branch: `codex/macos-inventory-20261013`. This document inventories source behavior; it does
not certify Android runtime behavior or implement desktop behavior. No Gradle/runtime tests
were run for this documentation task.

The [macOS plan](../design/explorations/AutPlay_macOS_Port_Plan_Draft_v1.md) is a draft.
The release allocations below are proposals for its stage 0 review, not newly approved product
commitments. **No functional parity is claimed.** All rows remain unverified on macOS.
The [baseline contracts inventory](MACOS_BASELINE_CONTRACTS.md) records compatibility blockers.

Allocation vocabulary:

- **First release**: proposed core scope from the plan, subject to implementation and qualification.
- **First release, dependent**: proposed scope requiring the named server/security/native work;
  a dependency is not an implemented result or permission to change frozen decisions.
- **Deferred**: explicitly outside this proposed first release; revisit in a later version.
- **Excluded**: outside the active product/port scope, with no planned desktop implementation.

Sources in the tables are relative links to baseline files. A UI action is evidence of the
current surface, not a claim that every backend, flag, role or network condition enables it.
Server-owned operations retain server authority. Android-specific gestures may become desktop
mouse/keyboard interactions while preserving commands and ownership.

## Local library and playback

| ID | Android function and source evidence | Proposed allocation | Dependency / desktop closure criterion |
| --- | --- | --- | --- |
| L01 | Selected audio import and resumable review jobs: [import UI][import-ui], [review repository][import-repo] | First release | File chooser and bounded probing; managed verified copy policy from the draft, source preserved; cancel/retry and duplicate import verified. |
| L02 | MediaStore phone music access, SAF folder choice/rescan: [phone repository][phone], [tree scanner][scanner], [settings][settings] | First release | Replace Android permissions/URI access with macOS file/folder access; unavailable/moved/external sources must be repairable. MediaStore itself is Android-only. |
| L03 | Import matching: accept/reject candidate, keep unresolved, create recording where allowed; pause/resume/cancel job: [import UI][import-ui], [review repository][import-repo] | First release, dependent | Preserve unresolved identity and user choice; server resolver requires compatible authenticated transport. Offline import must remain usable. |
| L04 | Permanent library: tracks/artists/albums/playlists/offline/unavailable/review; recent/title/artist sort; all/loved/downloaded/available/unavailable filters: [core screens][core-ui], [view state][core-state], [repository][core-repo] | First release | Desktop SQLite, atomic mutations and source resolution; restarting offline preserves library. |
| L05 | Track/album/artist/playlist details, artist credits/appearances: [detail UI][details], [album grouping][albums] | First release | Preserve stable IDs and local/remote availability distinctions; do not infer artist identity from a display name. |
| L06 | Remove/restore library track, liked collection and preference actions: [detail UI][details], [library repository][library] | First release | Local transaction + Journal/outbox; remove from library is distinct from deleting owned bytes or account. |
| L07 | Local text search and track/album/artist/playlist search kinds: [search UI][core-ui], [FTS repository][search], [kind picker][search-kind] | First release | FTS/Unicode/empty/error states on real desktop SQLite, with no server dependency for local search. |
| L08 | Manual playlist create, name/description edit, delete, add/remove/reorder entries and play from entry: [playlist editor][playlists] | First release | Preserve entry IDs, ordering and duplicate entries; offline restart and mutations verified. |
| L09 | Listening history: paged display, retry/load more, play/open item: [history UI][history-ui], [history repository][history] | First release | Session/listening identity and event attribution preserved across restart; no double history event. |
| P01 | Mini-player/full player, play/pause, previous/next, seek: [player][player], [session owner][session] | First release | Real ARM64 audio engine and seek/transition/error tests; UI state alone is insufficient. |
| P02 | Shuffle/repeat, play next/add to queue, move/remove/clear upcoming: [player][player], [queue UI][queue], [detail UI][details] | First release | Stable queue entry IDs including duplicates, one queue owner; repeat/shuffle restoration tested. |
| P03 | Queue/position/checkpoint restoration and playback continuation: [persistence][playback-persistence], [shutdown persistence][shutdown] | First release | Checkpoint on pause/seek/error/teardown plus periodic saving; explicit desktop autostart policy; crash/restart proof. |
| P04 | Like/dislike/clear preference, exclude current listen or session from taste: [player][player], [library repository][library] | First release | Keep preference, per-listen and session exclusion distinct; local durable facts and sync attribution retained. |
| P05 | Sleep timer, stop after current track and cancel timer: [player][player] | First release | Monotonic clocks and desktop sleep/wake policy; no late timer callback after session replacement. |
| P06 | Smooth track visual transitions setting and artwork carousel/swipes: [settings][settings], [player][player] | First release | Desktop presentation adaptation; this setting is not evidence of audio crossfade/gapless codec support. |
| P07 | Android MediaSession/service/notification and system playback commands: [playback service][service] | First release | Native macOS media keys, title/artist/artwork, Dock/window/Quit and sleep/wake qualification; Android service is not portable. |
| P08 | Audio file recognition: `.mp3`, `.m4a`, `.aac`, `.flac`, `.ogg`, `.opus`, `.wav`, or `audio/*`: [tree scanner][scanner] | First release | Recognition is not a decoder support guarantee. Stage 1 must test MP3, AAC/M4A, FLAC, Opus, PCM WAV on ARM64; Ogg/container combinations need explicit engine evidence. |

## Appearance, recommendations and collaborative listening

| ID | Android function and source evidence | Proposed allocation | Dependency / desktop closure criterion |
| --- | --- | --- | --- |
| U01 | Light/dark themes and RU/EN selection: [settings][settings], [language][language], [EN resources][en], [RU resources][ru] | First release | Shared resources and desktop layout/focus/accessibility; persist selection. |
| U02 | Home continuation, releases, recommended tracks, library shortcuts and adaptive navigation: [core screens][core-ui], [destinations][routes] | First release | Populate core local surfaces without network; recommendation slots depend on R01/R02 and must have honest unavailable states. |
| U03 | Welcome onboarding and library authorization guidance: [onboarding][onboarding], [settings][settings] | First release | Desktop-specific access explanation, keyboard navigation and resize; no Android permission wording on Mac. |
| R01 | My Wave queue and online personalized recommendations: [My Wave][mywave], [recommendation repository][recommendations], [server UI][server-ui] | Deferred | Later version after local player, compatible accounts and recommendation transport; no implicit desktop promise from a Home slot. |
| R02 | Owner-scoped offline recommendation packs, temporal delta and presentation/feedback attribution: [recommendation repository][recommendations], [temporal delta][temporal] | Deferred | Later version needs pack integrity/expiry, account isolation, immutable parent bytes and offline feedback proof. |
| R03 | Exact/algorithmic recommendation replay exposed by server tools: [server UI][server-ui] | Deferred | Later diagnostics scope; preserve server result identity and replay semantics. |
| W01 | Wave room create/join by code/leave/close and host transfer: [Wave coordinator][wave] | Deferred | Later version needs macOS clock/audio proof and device compatibility; no local recreation of server room authority. |
| W02 | Host queue/transport commands, scheduled playback, readiness/preflight, reconnect/recover: [Wave coordinator][wave], [Wave core][wave-core] | Deferred | Later version must verify host/follower command restrictions, timing and media readiness on real devices. |
| W03 | Room prefetch OFF/NEXT/NEXT_3/AGGRESSIVE_WIFI: [settings][settings], [Wave coordinator][wave] | Deferred | Depends on W01/W02 and desktop managed downloads/network policy. |
| W04 | Issue guest document, guest join/cancel/leave, active/expired/revoked states and guest playback: [Wave coordinator][wave], [guest UI][guest], [guest runtime][guest-runtime] | Deferred | Later version retains process-local guest authority, expiry and sanitized projections; guest access grants no account/Vault bypass. |
| S01 | Public ID registration and exact same-server lookup; contact card create/share/import: [social panel][social], [public ID panel][public-id] | Deferred | Later server-enabled release; desktop share/file/code adapters and exact identity validation. |
| S02 | Friend requests accept/decline/cancel, remove, block/unblock and incoming/outgoing/confirmed lists: [social panel][social], [social runtime][social-runtime] | Deferred | Later version; preserve server-owned graph and explicit actions, with unavailable/error states. |
| S03 | Presence privacy settings and friend room invitation accept/cancel: [social panel][social] | Deferred | Later version depends on social and Wave; no inferred presence or automatic sharing. |
| S04 | Friend statistics visibility and view: [social panel][social], [statistics cards][stats-ui] | Deferred | Later version with confirmed privacy settings and authorization-negative tests. |

## Metadata, transfers, acquisition and consent

| ID | Android function and source evidence | Proposed allocation | Dependency / desktop closure criterion |
| --- | --- | --- | --- |
| M01 | Embedded/local tags displayed with metadata state and provenance: [metadata panel][metadata-ui], [metadata repository][metadata] | First release | Desktop embedded metadata reader and bounded artwork decoder; distinguish local/user/provider values. |
| M02 | Server metadata refresh, candidate selection and manual title/artist/album/date/label/track/disc edit: [metadata panel][metadata-ui], [metadata work][metadata-work] | Deferred | Current controls require active profile and WorkManager command execution. Later desktop scheduler/transport must preserve revision conflict and retry rules. |
| M03 | Cached metadata artwork display: [metadata panel][metadata-ui] | First release | Own bounded cache/path validation and desktop image decoding; no copying Android absolute paths. |
| M04 | Arbitrary cover replacement or write edited tags into original audio bytes | Deferred | Baseline [metadata panel][metadata-ui] exposes field edit/refresh/select, not a cover picker or file-tag writer. No implemented Android cover-edit parity is inferred; future feature requires separate scope. |
| T01 | Non-secret settings JSON export/import: [settings][settings], [settings codec][settings-codec] | First release | Desktop file adapter and same allowlist; credentials, account passport and platform-local file access are not portable settings. |
| T02 | Server import job choose/status/page/cancel/resume and review: [server UI][server-ui], [server repository][server-repo] | Deferred | Later version reuses server jobs; no local ownership of server paths/import execution. |
| T03 | Selected eligible local track Vault upload/cancel/resume intent: [server UI][server-ui], [upload intent][upload] | Deferred | Later version after desktop file hashing, compatible credentials and server upload authorization. |
| T04 | Authorized Vault streaming, source resolution and runtime credentials: [Vault source][vault], [source resolver][resolver] | First release, dependent | Compatible server + Keychain signer/session; actual Range/seek/redirect/expiry tests. Do not persist URL/token in queue. |
| T05 | Managed downloads: selected track, progress/status, cancel/retry/play offline: [downloads UI][downloads], [download repository][download-repo] | First release, dependent | Stage 6 execution/index/media port, checksum, partial/resume/disk-full and byte-availability verification. |
| T06 | Metered-network download preference and stream cache versus retained downloads: [settings][settings], [download policies][download-policies] | First release, dependent | Desktop network/quota adapter; pinned downloads must survive ordinary cache eviction. |
| D01 | Internet music source search and metadata discovery/catalogue context: [music search][internet], [metadata discovery][discovery-ui] | Deferred | Later version; provider acquisition authorization and bounded transport, no DRM bypass or automatic scraping promise. |
| D02 | Artist discovery policy edit, start discovery and candidate decision/import modes: [automation panel][automation] | Deferred | Later version gated by compatible server capabilities/roles; keep pending operation/idempotency and server-owned execution. |
| C01 | Training consent status and explicit grant/revoke commands: [consent card][consent-ui], [consent runtime][consent] | First release, dependent | Expose confirmed server state only when supported; no assumed consent, durable command replay and account scoping. Native/account prerequisites apply. |

## Account, pairing, devices and privacy

| ID | Android function and source evidence | Proposed allocation | Dependency / desktop closure criterion |
| --- | --- | --- | --- |
| A01 | Local portable account creation and public ID/password identity: [portable runtime][portable], [account UI][portable-ui] | First release, dependent | Keychain/device signer, portable crypto fixture agreement; root account identity separate from new Mac device ID/key. |
| A02 | Connect/register/claim server copy, login and interrupted enrollment resume: [portable runtime][portable], [portable operations][portable-ops] | First release, dependent | Stage 4 versioned platform compatibility and feature flag; Mac must never masquerade as Android. |
| A03 | Portable recovery-file save/import, recovery code, offline recovery eligibility and root rotation: [portable crypto][portable-crypto], [portable runtime][portable], [portable UI integration][portable-main] | First release, dependent | Cross-device v1/v2 fixtures, lineage, corrupt/wrong-password/replay tests, secure document flow; never include recovery material in ordinary exports. |
| A04 | Server-bound account recovery export/recipient recovery: [recovery protocol][recovery], [recovery UI integration][recovery-main] | First release, dependent | Distinct from portable recovery; compatible recipient platform and binding committer, trust, damaged-document and interrupted-operation proof. |
| A05 | Password reauthentication for sensitive lifecycle actions and recovery rotation: [portable runtime][portable], [profile UI][profile] | First release, dependent | Preserve account/device/session authority and first-bind ceremony; no frozen security decision change in inventory. |
| A06 | Profile discovery, trust evidence confirmation, admission, invitation exchange and local-data choice/review: [profile UI][profile], [profile transport][profile-port] | First release, dependent | Explicit MACOS contract extension/ADR and trusted server identity; original Android wire compatibility retained. |
| A07 | QR/code invitation input and QR camera scanner: [profile UI][profile], [scanner activity][qr] | First release, dependent (file/code input); camera Deferred | Image/code import must support daily-use pairing without camera. Camera access/UX is a separate later capability. |
| A08 | Self-device pairing source/recipient, SAS confirmation, approve/cancel and resume: [self pairing][self], [self UI integration][self-main] | First release, dependent | Android-only server recipient/filter must change through approved compatibility work; cross-device proof and quota/revocation tests. |
| A09 | Device/session lists, current/all-session logout, current-device revoke, local disconnect, trusted re-enrollment and invitation create/cancel lifecycle: [profile UI][profile], [profile transport][profile-port] | First release, dependent | Remove Android-only list assumptions through versioned work; preserve unknown future platform values and explicit reauthentication. |
| A10 | Public invitation account registration and owner provisioning/create/cancel invitation/disable account: [profile UI][profile] | Deferred | Later admin/role scope; not required to make local desktop player usable. |
| A11 | Sync status/retry, bootstrap, offline Journal/outbox, conflicts and multi-device history/preferences/playlists: [sync engine][sync], [sync transport][sync-port] | First release, dependent | Compatible device contract and durable desktop scheduler; preserve lineage/event hashes, ownership and atomic mutation; app Quit does not imply background execution. |
| V01 | Owner statistics including portable-account aggregate and local history: [profile][profile-main], [statistics repository][stats], [portable statistics][portable-stats] | First release, dependent | Preserve event alias deduplication, account isolation and snapshot ownership; cross-device aggregate tests. |
| V02 | Share profile statistics privacy toggle: [settings][settings], [statistics cards][stats-ui] | First release, dependent | Confirmed server setting, default unavailable/private display and update failure behavior. Friend view remains S04 deferred. |
| V03 | Account deletion source/recipient ceremony, cancellation/status/resume: [deletion UI integration][delete-main], [deletion protocol][delete] | First release, dependent | Compatible platform, accepted privacy authority and lifecycle gates; account ceremony is not ordinary local library removal or unilateral byte purge. |
| V04 | Ordinary personal-data export and physical privacy purge: [privacy runbook][privacy], [portable operations][portable-ops] | Deferred (new desktop archive/purge UI) | Existing operator-assisted owner procedure remains authoritative; internal portable page helper is not a full export endpoint. No production deletion/export performed here. |
| V05 | Privacy/data route and non-secret settings controls: [routes][routes], [MainActivity][main], [settings][settings] | First release | At baseline MainActivity maps PrivacyAndData to Settings (line 2599), not a delivered full archive exporter. Keep claims aligned with V03/V04/T01. |
| X01 | Server health, developer diagnostics and detailed remote library/search tools: [server UI][server-ui], [settings][settings] | Deferred (developer tools); basic connection status First release | Developer-gated views do not establish normal user playback availability. Later diagnostic scope must remain redacted. |
| X02 | AutPlay Face | Excluded | Draft explicitly excludes Face from active product. Historical contracts/code do not restore it to desktop scope. |
| X03 | Install server/GPU on Mac; iOS; Windows/Linux desktop; Intel/x64 release | Excluded from this proposed release | Remote existing server only. Mac ARM64 required; x64 support needs a separate native/package/test matrix before any claim. |

## Qualification and decision handoff

The initial proposed release is the local player/library plus compatible account/sync/Vault,
managed downloads, consent/privacy/account lifecycle surfaces and native macOS integration.
My Wave, offline recommendations, rooms/guests, social, online tag editing, uploads/server imports
and discovery are explicitly deferred here for review. Changing that allocation requires an
explicit product decision and implementation/verification scope; it cannot silently become a
parity promise. The draft's stages 4–7 dependencies remain required for dependent rows.

Use these row IDs in later evidence. A row closes only with exact package/source/server identity,
actual result and known limits, or an explicitly accepted exclusion. Real **Mac ARM64** is needed
early for audio/native/Keychain/packaging and later Gatekeeper, media keys, external disks,
sleep/wake and update qualification. This Windows source inventory supplies none of that proof.
Minimum macOS version and Intel/x64 support are not established by this document.

[import-ui]: ../../apps/android/src/main/kotlin/app/autplay/ui/LegacyImportRoute.kt
[import-repo]: ../../apps/android/src/main/kotlin/app/autplay/application/importing/LocalImportReviewRepository.kt
[phone]: ../../apps/android/src/main/kotlin/app/autplay/application/importing/PhoneMusicRepository.kt
[scanner]: ../../apps/android/src/main/kotlin/app/autplay/application/importing/ContentTreeAudioScanner.kt
[settings]: ../../apps/android/src/main/kotlin/app/autplay/ui/settings/SettingsProductScreen.kt
[core-ui]: ../../apps/android/src/main/kotlin/app/autplay/ui/CoreProductScreens.kt
[core-repo]: ../../apps/android/src/main/kotlin/app/autplay/application/library/CoreProductRepository.kt
[core-state]: ../../apps/android/src/main/kotlin/app/autplay/ui/core/CoreProductState.kt
[details]: ../../apps/android/src/main/kotlin/app/autplay/ui/CoreProductDetailScreen.kt
[albums]: ../../apps/android/src/main/kotlin/app/autplay/application/library/AlbumGroupRepository.kt
[library]: ../../apps/android/src/main/kotlin/app/autplay/application/library/LibraryVerticalSliceRepository.kt
[search]: ../../apps/android/src/main/kotlin/app/autplay/application/search/LocalTrackSearchRepository.kt
[search-kind]: ../../apps/android/src/main/kotlin/app/autplay/ui/LibrarySearchKindPicker.kt
[playlists]: ../../apps/android/src/main/kotlin/app/autplay/ui/playlist/ManualPlaylistEditor.kt
[history-ui]: ../../apps/android/src/main/kotlin/app/autplay/ui/history/HistoryScreen.kt
[history]: ../../apps/android/src/main/kotlin/app/autplay/application/history/HistoryRepository.kt
[player]: ../../apps/android/src/main/kotlin/app/autplay/ui/player/PlaybackPlayerSurfaces.kt
[session]: ../../apps/android/src/main/kotlin/app/autplay/playback/PlaybackSessionOwner.kt
[queue]: ../../apps/android/src/main/kotlin/app/autplay/ui/queue/QueueEditorPanel.kt
[playback-persistence]: ../../apps/android/src/main/kotlin/app/autplay/application/playback/PlaybackPersistenceRepository.kt
[shutdown]: ../../apps/android/src/main/kotlin/app/autplay/playback/PlaybackShutdownPersistence.kt
[service]: ../../apps/android/src/main/kotlin/app/autplay/playback/AutPlayPlaybackService.kt
[language]: ../../apps/android/src/main/kotlin/app/autplay/ui/AppLanguage.kt
[en]: ../../apps/android/src/main/res/values/strings.xml
[ru]: ../../apps/android/src/main/res/values-ru/strings.xml
[routes]: ../../apps/android/src/main/kotlin/app/autplay/ui/UiDestination.kt
[onboarding]: ../../apps/android/src/main/kotlin/app/autplay/ui/WelcomeOnboardingScreen.kt
[mywave]: ../../apps/android/src/main/kotlin/app/autplay/MyWaveQueue.kt
[recommendations]: ../../apps/android/src/main/kotlin/app/autplay/application/recommendation/OfflineRecommendationRepository.kt
[temporal]: ../../apps/android/src/main/kotlin/app/autplay/application/recommendation/OfflineTemporalDelta.kt
[server-ui]: ../../apps/android/src/main/kotlin/app/autplay/ui/ServerFeaturesScreen.kt
[wave]: ../../apps/android/src/main/kotlin/app/autplay/application/wave/WaveCoordinator.kt
[wave-core]: ../../apps/android/src/main/kotlin/app/autplay/domain/wave/WaveCore.kt
[guest]: ../../apps/android/src/main/kotlin/app/autplay/ui/guestroom/GuestRoomScreen.kt
[guest-runtime]: ../../apps/android/src/main/kotlin/app/autplay/application/guestroom/GuestRoomRuntime.kt
[social]: ../../apps/android/src/main/kotlin/app/autplay/ui/social/SocialPanel.kt
[public-id]: ../../apps/android/src/main/kotlin/app/autplay/ui/social/SocialPublicIdPanel.kt
[social-runtime]: ../../apps/android/src/main/kotlin/app/autplay/application/social/SocialRuntime.kt
[stats-ui]: ../../apps/android/src/main/kotlin/app/autplay/ui/statistics/ProfileStatisticsCard.kt
[metadata-ui]: ../../apps/android/src/main/kotlin/app/autplay/ui/TrackMetadataPanel.kt
[metadata]: ../../apps/android/src/main/kotlin/app/autplay/application/library/TrackMetadataRepository.kt
[metadata-work]: ../../apps/android/src/main/kotlin/app/autplay/work/TrackMetadataWorker.kt
[settings-codec]: ../../apps/android/src/main/kotlin/app/autplay/application/settings/SettingsTransferCodec.kt
[server-repo]: ../../apps/android/src/main/kotlin/app/autplay/application/server/ServerFeatureRepository.kt
[upload]: ../../apps/android/src/main/kotlin/app/autplay/data/local/entity/Entities.kt
[vault]: ../../apps/android/src/main/kotlin/app/autplay/playback/VaultPlaybackDataSource.kt
[resolver]: ../../apps/android/src/main/kotlin/app/autplay/playback/AndroidPlaybackSourceResolver.kt
[downloads]: ../../apps/android/src/main/kotlin/app/autplay/ui/downloads/DownloadsScreen.kt
[download-repo]: ../../apps/android/src/main/kotlin/app/autplay/application/download/DownloadIntentRepository.kt
[download-policies]: ../../apps/android/src/main/kotlin/app/autplay/download/DownloadPolicies.kt
[internet]: ../../apps/android/src/main/kotlin/app/autplay/ui/InternetMusicSearchSection.kt
[discovery-ui]: ../../apps/android/src/main/kotlin/app/autplay/ui/InternetMetadataDiscoverySection.kt
[automation]: ../../apps/android/src/main/kotlin/app/autplay/ui/DiscoveryAutomationPanel.kt
[consent-ui]: ../../apps/android/src/main/kotlin/app/autplay/ui/settings/TrainingConsentCard.kt
[consent]: ../../apps/android/src/main/kotlin/app/autplay/application/trainingconsent/TrainingConsentRuntime.kt
[portable]: ../../apps/android/src/main/kotlin/app/autplay/application/portableaccount/PortableAccountRuntime.kt
[portable-ui]: ../../apps/android/src/main/kotlin/app/autplay/ui/profilepairing/PortableAccountCard.kt
[portable-ops]: PORTABLE_ACCOUNTS.md
[portable-crypto]: ../../apps/android/src/main/kotlin/app/autplay/application/portableaccount/PortableAccountCrypto.kt
[portable-main]: ../../apps/android/src/main/kotlin/app/autplay/MainPortableAccountUi.kt
[recovery]: ../../apps/android/src/main/kotlin/app/autplay/application/accountrecovery/AccountRecoveryProtocol.kt
[recovery-main]: ../../apps/android/src/main/kotlin/app/autplay/MainAccountRecoveryUi.kt
[profile]: ../../apps/android/src/main/kotlin/app/autplay/ui/profilepairing/ProfilePairingScreen.kt
[profile-port]: ../../apps/android/src/main/kotlin/app/autplay/application/profilepairing/OkHttpProfilePairingPort.kt
[qr]: ../../apps/android/src/main/kotlin/app/autplay/EnrollmentQrScannerActivity.kt
[self]: ../../apps/android/src/main/kotlin/app/autplay/application/selfpairing/SelfPairingProtocol.kt
[self-main]: ../../apps/android/src/main/kotlin/app/autplay/MainSelfPairingUi.kt
[sync]: ../../apps/android/src/main/kotlin/app/autplay/application/sync/SyncEngine.kt
[sync-port]: ../../apps/android/src/main/kotlin/app/autplay/application/sync/OkHttpSyncTransport.kt
[profile-main]: ../../apps/android/src/main/kotlin/app/autplay/UserProfileScreen.kt
[stats]: ../../apps/android/src/main/kotlin/app/autplay/application/statistics/ProfileStatisticsRepository.kt
[portable-stats]: ../../apps/android/src/main/kotlin/app/autplay/application/statistics/PortableAccountStatisticsRepository.kt
[delete-main]: ../../apps/android/src/main/kotlin/app/autplay/MainAccountDeletionUi.kt
[delete]: ../../apps/android/src/main/kotlin/app/autplay/application/accountrecovery/AccountDeletionProtocol.kt
[privacy]: PRIVACY_DELETE_EXPORT.md
[main]: ../../apps/android/src/main/kotlin/app/autplay/MainActivity.kt

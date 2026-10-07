# Combined Android playback verification — 2026-10-07

This record covers the main-checkout Android changes combined with the separately developed
three-second transition cuts. It describes a debug QA artifact, not a published APK release.
Machine-local manifests, logs and frozen APKs are retained privately; hashes identify them below.

## Behavior verified

- A media-notification tap opens Now Playing.
- Native feedback actions are ordered **Dislike / Like**. Both persist ratings for the track
  captured by the notification action, including when the active track changes.
- Horizontal cover swipes navigate both directions in Now Playing.
- With the transition option enabled, ordinary User, Search, Library and Playlist queues cut
  three seconds from a transitioning track's tail and its successor's head, without volume fading.
- The first/restored track keeps its original head/position. The final track and stop-after-item
  retain their tail. Sources of at most six seconds, unknown duration, live or unseekable sources
  play whole. Successor selection respects playable items, shuffle and repeat.
- Playback statistics use original recording coordinates and exclude skipped audio.
- Retired Android Face code remains absent from all 20 DEX files of the combined debug APK.

## Artifact identity

| Artifact | SHA-256 |
| --- | --- |
| `combined-debug.apk` | `cadafe6f547e812f9628edb9d55475d0d02b3f46d2bef606dff1ab7eb180635f` |
| `combined-androidTest.apk` | `663ccdd71aba18582b828965f72acc203f7b1138ef49ea12539e841f51810825` |
| `source-manifest.json` — 516 Android/Gradle inputs, including tests | `6e109dc5de41a88f2bb6d72fa760abd37c9d331868dce0b604cad97d11e7a099` |
| Reviewed transition patch | `2fd13db4992040c56713b86a293354871de8c47ecf11e8023ad3c48318c42adb` |

All manifest inputs matched before instrumentation and after verification.

## Checks and limits

- Pinned Gradle Wrapper with JDK 17.0.20+8, strict dependency verification, one worker,
  no parallel execution and the repository's 2 GiB heap: `lintDebug`, `testDebugUnitTest`,
  `assembleDebug`, `assembleDebugAndroidTest`, `assembleTrustedLan` and `assembleRelease` passed.
- **493 unit tests passed**, with zero failures, errors or skips.
- **43 selected Android tests passed** on a disposable API 26 emulator:
  `PlaybackNotificationNavigationTest`, `PlaybackServiceLifecycleTest`,
  `Media3PlaybackDeviceTest` and `PlaybackPlayerSurfacesTest`.
- Lint: zero errors or warnings; one existing `AutoboxingStateCreation` Hint in the debug
  visual-evidence fixture. The build also reported the existing nonfatal SDK XML-version warning.
- The emulator and its AVD were removed after the tests. No user phone data was cleared.

The combined APK was not installed on the physical A55 during this integration check.
Earlier physical-phone results remain tied to their earlier artifacts. This selected test run
does not claim the full scheduled Android connected suite or production-release qualification.
The public APK remains the separate 1.0.5 prerelease.

## Other main-checkout checks before Git publication

- Canonical `scripts/check.ps1 -ServerOnly` returned exit 0: **311 root tests passed**;
  **2610 server tests passed, 76 skipped**, with one warning. The server run used disposable
  PostgreSQL 18.4 / pgvector 0.8.6 and took 1941.14 seconds. Its Compose container, volume
  and network were removed; an independent label-scoped inventory found zero remaining resources.
- Server lock, Ruff, formatting and strict mypy checks passed. Windows skips cover symlink
  privileges and Linux-specific cgroup/media/IPC behavior; they do not establish Linux coverage.
  Earlier targeted Linux evidence keeps the scope stated in the
  [metadata verification report](../operations/METADATA_ALBUMS_SEARCH_2026-10-06.md).
- Acquisition lock, Ruff, formatting and mypy passed; **332 tests passed, eight skipped**.
  The separate tools tests ran in their owning frozen environments: 36 in the server environment
  and three source-wire cases in the acquisition environment. All 39 passed.
- The preserved historical audio-input experiment's 17 provenance tests passed separately.
- Production-source secret scanning passed. Publication review removed machine-local evidence
  paths and private host labels; remaining scan matches are synthetic fixtures or documented examples.
- The README's original screenshot is preserved, and its final PNG was inspected at desktop
  and phone widths in light and dark themes. Local links and asset auditing passed.

These counts describe separate suites and are not combined into a larger unique total.
GitHub's subsequent Linux CI remains a separate run.

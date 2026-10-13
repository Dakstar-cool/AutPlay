# Desktop shell foundation

This block implements the first desktop UI slice from the
[macOS port plan](../design/explorations/AutPlay_macOS_Port_Plan_Draft_v1.md).
It is a foundation shell, not yet the standalone local player described by stage 3.

## Entry point and integration

The JVM entry point is `app.autplay.desktop.MainKt`, in
[`Main.kt`](../../apps/desktop/src/main/kotlin/app/autplay/desktop/Main.kt).
It opens an AutPlay window at 1080 x 740 dp with a minimum AWT size of 860 x 600.
Closing the window exits the application. Normal system window decorations are retained.

The integrated module setup is:

| Module | Source sets and dependencies |
| --- | --- |
| `shared/ui` | KMP JVM target; UI in `commonMain`; Compose runtime, foundation, UI and Material3; `shared/application` and domain as agreed by the coordinator |
| `shared/ui` tests | `jvmTest`; JUnit 4.13.2 (existing `libs.junit4`) |
| `apps/desktop` | JVM `src/main/kotlin`; `shared/ui`, `shared/application`, Compose runtime/UI/desktop `currentOs`, coroutines core |

Use the agreed Kotlin 2.4.10, JDK 17 and Compose Multiplatform 1.12.1 toolchain.
The desktop `mainClass` is `app.autplay.desktop.MainKt`. No icons, generated Compose
resources, Android libraries or additional native media libraries are required by this UI.

The shell uses these extracted contracts with their original packages:

- `app.autplay.playback.presentation.PlaybackPresentationState` and its control/connection enums.
- `app.autplay.application.playback.ActiveQueueContext`.
- `app.autplay.platform.PlaybackPort`: `state: StateFlow<PlaybackPresentationState>` and
  non-suspending `play()`, `pause()`, `seekTo(Long)`, `next()`, `previous()` commands.

`DesktopApp(playbackPort)` observes the adapter's state and forwards commands.
The default entry point supplies no adapter. Its state is disconnected, with an absent queue
and `NO_ACTIVE_QUEUE` control lock. This is unavailable playback, not a simulated player.

## Behavior and ownership

[`AutPlayDesktopShell`](../../shared/ui/src/commonMain/kotlin/app/autplay/ui/desktop/AutPlayDesktopShell.kt)
renders supplied state and emits callbacks. It does not store tracks, queue entries, playback
position, import results or server state. Shared UI has no Android, Java, storage or port imports.

- The sidebar opens Library, Playlists and Settings. Library and playlist screens show honest
  empty states. Import and playlist creation are disabled and explain the missing local storage.
- Settings switches between light and dark themes and Russian and English text. Initial language
  follows the JVM locale (`ru` uses Russian; other languages use English); initial theme is dark.
  These preferences and navigation exist only during the current process. The settings screen
  explicitly discloses that lifetime.
- Colors, rounded shapes and typography follow the Android product's default Coral appearance,
  without Android system-bar code. Other accent choices are deferred.
- Playback displays supplied title, artist, position and duration. All commands require an
  available adapter, a connected presentation state, an allowed application control gate and
  a current media ID. Previous/next also require their respective entry IDs. Seek also requires
  a positive duration and the supplied seek gate, seekability and non-live state.
- Seek emits an absolute millisecond position bounded by duration. It does not optimistically
  change playback state. Unknown duration displays `--:--` rather than a fabricated duration.
- Material controls and selectable sidebar items support mouse and keyboard focus. Main content
  scrolls when the window is short. Playback titles and subtitles truncate rather than stretching
  the controls. Seek and time fields carry localized accessibility descriptions.

Native file selection, managed imports, a persistent library, playlist mutations, account/server
connections, audio adapters, media keys and saved preferences are deferred. The shell does not
claim successful import, persistent storage, audible playback or macOS release qualification.

## Verification and next gate

Three focused JVM tests in
[`DesktopShellPolicyTest.kt`](../../shared/ui/src/jvmTest/kotlin/app/autplay/ui/desktop/DesktopShellPolicyTest.kt)
cover fail-closed controls (including every lock reason), navigation/seek availability and RU/EN
copy completeness. The integration Gradle gate runs these alongside 18 domain/application
tests on the extracted modules with strict dependency verification. Compose UI and the
desktop entry point compile on both the Windows development host and ARM64 macOS CI.

ARM64 macOS CI packages a development `.app` and `.dmg` with its own JVM. A bounded
`--smoke-test` startup check uses the packaged executable, awaits two Compose frames,
checks the visible window and bundled runtime, then closes the process. Current measured
results and source revision are recorded in [port status](MACOS_PORT_STATUS.md).

Interactive review on a Mac is still required for:

1. All three sections, both themes and both languages, including resize at the minimum size.
2. Tab/Shift+Tab and activation of navigation/theme/language controls, plus accessibility labels.
3. Disabled import, playlist creation, playback and seek with no adapter; no file or DB writes.
4. Fresh process resets session preferences and retains the honest empty state.

Clean installation, screen-reader behavior and real playback require later macOS evidence.
See the official [Compose window documentation](https://kotlinlang.org/docs/multiplatform/compose-desktop-top-level-windows-management.html)
for the window host API.

# macOS port execution

Implementation started 2026-10-13 on `codex/macos-port-20261013`.

## Source and working boundaries

Source snapshot: `102380418421f87679732406f8052150e8226da8`.
Parent checkout HEAD: `cdab71d2f853cb1d3a54cc338304cf398dbac7d8`.
The snapshot includes the current tracked and non-ignored product changes (202 Git status entries).
It was created through a temporary Git index; the original checkout branch, index and worktree
status were preserved. Normal Git text normalization applies to the reproducible snapshot.

Integration branch lives in its own worktree. Each new workstream uses a separate worktree from
the same snapshot. No older chat was reused. Build configuration, integration and sequential
Gradle execution are owned by the coordinator.

| Workstream | Reasoning level | Output |
| --- | --- | --- |
| Common domain and application | high | Existing policies, presentation state and playback boundary extracted once |
| Desktop shell | medium | Compose window, library/playlist/settings shell, RU/EN and light/dark themes |
| Audio and native spike | xhigh | Engine comparison and executable AVFoundation/HTTP probe on ARM64 macOS |
| Feature and contract inventory | medium | 63 feature rows and current persistence/device compatibility blockers |
| Independent foundation review | high | Build, policy and automatic regression gate review |
| Gradle resolution diagnostics | high | Confirmed repository timeout and PowerShell argument parsing fixes |
| Native cancellation diagnosis and review | high | Explicit transport stop, terminal acknowledgement and incomplete-transfer checks |

## Foundation scope

[ADR-056](../adr/ADR-056-macos-client-foundation.md) records the module decision.
[Feature matrix](MACOS_FEATURE_MATRIX.md) and [baseline contracts](MACOS_BASELINE_CONTRACTS.md)
describe the source and proposed scope; allocations remain proposals rather than a parity claim.
The [desktop shell](MACOS_DESKTOP_SHELL.md) describes the implemented presentation block.
The [native spike](MACOS_NATIVE_SPIKE.md) contains the engine comparison and bounded executable
AVFoundation probe; ten real socket fixture checks passed on Windows, including
delivery of a delayed incomplete body before cancellation.

The foundation adds shared modules and a desktop entry point, rather than completed local media
storage or audible playback. Real media execution, persistent imports/queues, Room desktop storage,
Keychain, account/Vault/sync, downloads and system controls remain later blocks. No device-platform
protocol or database migration is changed in this block. AutPlay Face remains excluded.

## Checks and Mac gate

- Source snapshot: 314 existing contract/release tests passed on pinned CPython 3.14.7.
- Source snapshot: Android lintDebug, testDebugUnitTest and assembleDebug passed with the pinned
  JDK and strict dependency verification; the full baseline run took 21m 41s.
- Integrated shared/desktop gate: 18 domain/application and 3 desktop control/copy tests passed
  with strict Gradle dependency verification. Compose UI and desktop JVM sources compiled.
- Android regression gate after extraction: 506 Android unit tests, all 21 shared tests, lintDebug,
  and debug, trusted-LAN and release APK builds (including R8) passed in 19m 23s.
- Android instrumentation APK compiled; instrumentation tests were not executed on a device.
- The final release-script checks passed: 127 tests on pinned CPython 3.14.7.
- All 1,058 baseline SHA-256 verification pins remain unchanged. Added platform/build artifacts
  have explicit checksums; strict verification remains enabled. Google Maven timeouts on the
  Windows host were handled using an isolated cache seeded by an official-repository CI run,
  rather than changing pinned tools or bypassing verification.
- Independent review identified that extracted tests must stay in the automatic Android host gate;
  the canonical check scripts now include shared policy tests.
- `scripts/check-desktop.ps1` and `scripts/check-desktop.sh` provide a focused strict-verification
  shared/desktop gate. `--package-macos` on the shell script requires a native ARM64 Mac.
- `CI / macOS desktop foundation` runs for the dedicated port branch and supports manual runs.
  It uses the hosted `macos-15` ARM64 runner, an exact
  Microsoft JDK 17.0.20 archive with official SHA-256, and development `.app`/`.dmg` packaging.
  It does not establish clean-install/Gatekeeper, signing/notarization, actual sound, screen-reader,
  media-key or sleep/wake qualification.
- The same Mac workflow compiles and runs the synthetic WAV AVFoundation probe, including its
  local auth/Range/redirect/drop/cancel scenarios. The other audio formats and JVM/native bridge
  remain part of the later engine qualification matrix.

First measured ARM64 Mac package run:
[38441068870](https://github.com/Dakstar-cool/AutPlay/actions/runs/38441068870),
source `f71da9b09cdd3c899e886033ca5a2eefbc43eb16`. Both `.app` and `.dmg` packaged
successfully on macOS 15.7.9 with the pinned Microsoft JDK. Six native WAV scenarios
passed; the slow cancellation case exposed an active HTTP task after item replacement.
The follow-up fix explicitly cancels owned tasks and waits for URLSession's terminal
acknowledgement.

Final ARM64 Mac gate: **PASS** in 5m 37s,
[38443531031](https://github.com/Dakstar-cool/AutPlay/actions/runs/38443531031),
source `ae9d5256726928a14144deb4a4da825c0dd3b85f`.
All 21 shared tests, desktop compilation and `.app`/`.dmg` packaging passed.
The packaged executable opened its window, advanced Compose frames and exited cleanly
using its bundled JVM. All seven synthetic WAV native scenarios passed: local playback,
authenticated Range playback, same-origin redirect, rejected 401/cross-origin redirect,
truncated body and cancellation. The cancellation report records one accepted incomplete
transfer cancelled, one terminal acknowledgement and zero remaining active tasks.
Audible sound and the missing MP3/AAC/M4A/FLAC/Opus matrix remain unverified. This transport
scenario does not qualify every native lifecycle/teardown path or the future JVM bridge.

[Development installer and evidence](https://github.com/Dakstar-cool/AutPlay/actions/runs/38443531031/artifacts/11837907936)
are retained by CI for seven days. `AutPlay-1.0.0.dmg` is an unsigned foundation package,
98,920,029 bytes. Its SHA-256 is
`de5ebe3cb260d45f7da0bff3e8038bb982a4fd3b89ebb86003df4c8274ce8587`.
The positive package version satisfies the JDK macOS bundler; it does not denote a
completed product release.

## Next dependencies

Complete the native audio/transport/Keychain evidence on a real Mac before selecting the media
engine. Then implement persistent local storage and source locators, import and local player as a
single usable block, followed by focused SQLite/audio acceptance checks. Versioned server device
support and credential/signature proof must precede account, sync and Vault integration.

[Implementation plan](../design/explorations/AutPlay_macOS_Port_Plan_Draft_v1.md) retains the full
stage order and release qualification requirements. Installer signing and production deployment
continue through existing release procedures.

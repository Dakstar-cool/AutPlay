# macOS local audio adapter

Date: 2026-10-13. This is the experimental local-file implementation of
`DesktopAudioEngine`, accepted for the desktop prototype only. It does not select
the production engine or close the codec, HTTP, Keychain or release gates in the
[port plan](../design/explorations/AutPlay_macOS_Port_Plan_Draft_v1.md).
The earlier [native spike](MACOS_NATIVE_SPIKE.md) is historical evidence for its
own source; it does not verify this new helper or its JVM adapter.
Implementation baseline: `703ede9334e1168433fbaf31a7b009489d35f263` on
`codex/macos-audio-20261013-local`.

## Integration and packaging

`app.autplay.desktop.audio.MacAudioEngine(helperExecutable: java.nio.file.Path)`
implements the fixed [desktop port](../../shared/application/src/commonMain/kotlin/app/autplay/application/desktop/DesktopLocalContracts.kt).
The host constructs one instance, obtains `managedPath` from
`DesktopLibraryStore.resolveManagedFile`, and awaits `close()` during teardown.
The adapter never owns a queue, library, history, credentials or persisted paths.
A JVM-wide lease rejects a second concurrent Mac engine with
`AUDIO_OWNER_ALREADY_ACTIVE`. Unsupported hosts publish
`UNAVAILABLE / AUDIO_UNSUPPORTED_PLATFORM` without starting a process.

The coordinator owns dependency declarations, resources, entry-point wiring and
CI. The Kotlin source uses the existing coroutines 1.10.2 and serialization JSON
1.11.0 pins, without a serialization compiler plugin. Build the Swift source on
an ARM64 Mac with the host Xcode command-line tools:

```bash
mkdir -p apps/desktop/build/native-resources/macos-arm64/native
xcrun swiftc -swift-version 5 -target arm64-apple-macosx13.0 \
  -sdk "$(xcrun --sdk macosx --show-sdk-path)" \
  -framework Foundation -framework AVFoundation -framework CoreMedia \
  apps/desktop/src/native/AutPlayAudioHelper.swift \
  -o apps/desktop/build/native-resources/macos-arm64/native/AutPlayAudioHelper
chmod 755 apps/desktop/build/native-resources/macos-arm64/native/AutPlayAudioHelper
```

Bundle that executable through the desktop `appResourcesRootDir` and resolve the
resulting resource path in the host. Resource copying can lose executable mode:
restore mode on the helper **inside the completed `.app` before building the
`.dmg`**, and verify `test -x`, `file` and `otool -L` on that packaged copy. The
helper uses Apple OS frameworks and the Swift runtime for macOS 13 or newer.
The package must supply the architecture-matching bundled JDK. This block does
not implement signing, notarization or an Intel binary.

The process launches directly through `ProcessBuilder` with the executable as
its only argv member. The managed media path is supplied only in a runtime stdin
frame. No shell, URL, bearer, cookie, credential store or network adapter is used.
Both native diagnostics and JVM-captured stderr are discarded. Exceptions are
mapped to fixed codes rather than exported as messages or stack traces.

## Protocol v1

Every message is a four-byte unsigned big-endian payload length followed by a
UTF-8 JSON object. Payload length is 1 through 65,536 bytes. Objects are flat;
duplicate keys, nested values, wrong types, unknown fields and out-of-range
integers are rejected. JSON integers are signed 64-bit values; IDs are positive,
generation and positions are nonnegative. The native helper validates exact
command members, and the JVM validates exact event members and fixed codes.

1. Helper sends `{"v":1,"type":"hello","protocol":"autplay.local-audio","maxFrameBytes":65536}`.
2. JVM sends the same hello; helper answers `{"v":1,"type":"ready"}`.
3. JVM sends commands with `v`, `type:"command"`, monotonically increasing `id`,
   `op`, and `generation`. `open` adds `managedPath`, `positionMs`, `autoplay`;
   `seek` adds `positionMs`. `play`, `pause`, `stop`, `close` have no extra members.
4. Helper acknowledges dispatch with `{"v":1,"type":"ack","id":...}`.
   This does not claim successful playback or seek. `close` is acknowledged
   after native cleanup, and the helper drains its writer then exits.
5. Events contain `v`, `type:"event"`, `generation`, `status`, `positionMs`,
   `isPlaying`; optional `durationMs`, `errorCode`, `title`, `artist` may be absent
   or null. There are no source/path/native-error-description event fields.

| Status | Meaning |
| --- | --- |
| `IDLE` | No selected native item; also the JVM's initial state |
| `BUFFERING` | Opening, readiness or seek is pending |
| `READY` | Native item ready; `isPlaying` reports observed transport and rate |
| `ENDED` | The current generation reached EOF, emitted once |
| `ERROR` | Local media operation failed with a fixed native code |
| `CLOSED` | Explicit teardown completed |
| `UNAVAILABLE` | JVM-only terminal transport/platform failure, with a fixed adapter code |

`isPlaying` is never set optimistically when a command is submitted. Native
position comes from `AVPlayer.currentTime`, duration from the loaded asset.
Title and artist are optional loaded tags, limited to 256 UTF-8 bytes each;
controls and path/URL-shaped tags are removed or omitted. Filenames are never a
metadata fallback. The library can retain its own title when native tags are
absent. Metadata loading is optional and bounded; its failure does not turn
playable audio into a media error.

## Ownership, limits and failure behavior

One AVPlayer and all playback state belong to the helper's main RunLoop.
Background pipe queues do not touch AVPlayer. Every item replacement invalidates
item/seek tokens, cancels asset and metadata tasks, cancels seeks/prerolls,
removes observers and releases the old item. Async completions check those tokens
and item identity before emitting. Close waits up to two seconds for cancelled
load owners before acknowledging; if cancellation does not settle, the helper
exits without claiming successful native cleanup. EOF is sticky: play/pause/seek after ENDED are
acknowledged and ignored; replay/repeat requires a fresh `open` generation.
`stop` and `close` remain valid after EOF.

The JVM serializes writes through one owner thread; event parsing uses a separate
reader. Generation acceptance, lifecycle and StateFlow changes are synchronized.
The first open permits generation 0; later opens must strictly increase it.
Old commands and callbacks cannot change the currently selected generation.
StateFlow may conflate intermediate frames; ENDED remains available until an
explicit subsequent command changes the generation or stops/closes execution.

| Bound | Prototype value / response |
| --- | --- |
| JVM command queue | 32 pending commands, one acknowledgement in flight; overflow terminates playback |
| Native pipe queues | 32 inbound commands awaiting main dispatch and 32 outbound frames; overflow exits after cleanup |
| Native outstanding load owners | 32 current/retired asset and metadata tasks, retained until completion; overflow closes the helper |
| Inbound JVM frame rate | 256 frames/second; excess is a protocol error |
| Handshake / acknowledgement / partial incoming JVM frame | 5 seconds; terminate helper on timeout |
| Native stdout frame write | 2 seconds; cleanup and exit on a blocked/broken pipe |
| Native asset/readiness | 10 seconds |
| Native seek / requested playback start | 5 seconds |
| Native playing without timeline progress | 10 seconds |
| JVM active event silence | 15 seconds while BUFFERING or actually playing |
| Optional metadata load | 2 seconds; inspect at most 128 loaded metadata items |
| Runtime managed path | Absolute canonical local path, at most 4,096 UTF-8 bytes; no NUL, symlink or nonregular file |
| Position / duration | At most 604,800,000 ms (7 days), prototype-only bound |
| JVM close | Bounded graceful command then process wait/termination and reader join; at most 12 seconds from caller |

Managed copies use neutral `<sha256>.media` filenames; the helper does not reject
or select codecs based on the basename suffix. AVURLAsset must inspect the actual
local content. The helper checks asset playability, protected-content status and an audio track;
unsupported media fails closed without transcoding. Local containers also use
`AVURLAssetReferenceRestrictionsKey` with `forbidAll` to prevent external media
references. See [Apple's reference restriction API](https://developer.apple.com/documentation/avfoundation/avurlassetreferencerestrictionskey).

Native media errors are `NATIVE_INVALID_PATH`, `NATIVE_FILE_UNAVAILABLE`,
`NATIVE_UNSUPPORTED_MEDIA`, `NATIVE_OPEN_FAILED`, `NATIVE_OPEN_TIMEOUT`,
`NATIVE_PLAYBACK_FAILED`, `NATIVE_SEEK_FAILED`, `NATIVE_SEEK_TIMEOUT`. A later
higher-generation open can recover from a native media error.

Adapter terminal errors are `AUDIO_UNSUPPORTED_PLATFORM`,
`AUDIO_OWNER_ALREADY_ACTIVE`, `AUDIO_HELPER_UNAVAILABLE`,
`AUDIO_INVALID_ARGUMENT`, `AUDIO_COMMAND_QUEUE_FULL`, `AUDIO_HANDSHAKE_TIMEOUT`,
`AUDIO_COMMAND_TIMEOUT`, `AUDIO_FRAME_TIMEOUT`, `AUDIO_EVENT_TIMEOUT`,
`AUDIO_PROTOCOL_ERROR`, `AUDIO_PROCESS_FAILED`, `AUDIO_PROCESS_EXITED`,
`AUDIO_TEARDOWN_TIMEOUT`. After terminal transport failure, commands do not
restart the helper; await close and construct a new owner explicitly. Close is
idempotent and completes cleanup under `NonCancellable` even if its caller's
coroutine is cancelled. Failed bounded teardown reports unavailable rather than
claiming successful native release. The JVM waits and, when necessary, kills
the process explicitly because [JDK Process](https://docs.oracle.com/en/java/javase/17/docs/api/java.base/java/lang/Process.html)
does not release it merely when references are dropped.

## Verification boundary

This source was implemented on Windows. Standalone compilation against cached
pinned Kotlin 2.4.10, JDK 17 and the shared desktop contract checks JVM syntax
without running Gradle. Windows fail-closed lifecycle is checked independently.
The native helper cannot be compiled or played on this host. Coordinator-owned
Gradle and actual ARM64 Mac/JVM acceptance are still required for this exact
source and packaged helper.

The focused Mac acceptance should prove a synthetic WAV in a neutral `.media`
managed filename can open, then exercise actual timeline
advance, pause drift, zero/middle/near-end seek, EOF once, replacement and stale
generation rejection, teardown while opening/seeking, helper death and a Unicode
managed path. Include a failed/missing native close acknowledgement and confirm
UNAVAILABLE persists after `close()` completes. Resolve test paths with
`toRealPath()` so temporary-directory aliases such as `/var` do not fail the
canonical-path guard. Exercise both the direct helper and packaged JVM adapter, confirm
stdout/log evidence contains no managed path, and verify no helper survives exit.
Audible output needs hardware evidence; advancing time alone does not establish
sound reaching speakers. The complete MP3/AAC/M4A/FLAC/Opus matrix, authenticated
HTTP, Keychain, system media controls and release qualification remain separate.

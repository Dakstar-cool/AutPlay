# macOS native/audio spike: evidence and next Mac run

Date: 2026-10-13. Baseline: `102380418421f87679732406f8052150e8226da8`.
Branch: `codex/macos-native-20261013`. This is stage 1 preparation for the
[macOS draft](../design/explorations/AutPlay_macOS_Port_Plan_Draft_v1.md), not a
production engine decision or a completed stage 1 gate.

## Current decision and evidence

Investigate AVFoundation first with the executable
[probe](../../tools/macos-audio-spike/README.md). Keep stable vlcj/LibVLC as the
comparison candidate. Neither is accepted for production. No Mac is connected to
this Windows host: Swift compilation, decoding, audible sound, JVM callbacks,
Keychain, `.app` and `.dmg` are **NOT RUN** here. Windows evidence proves only the
disposable HTTP fixture contract and Python checks. A native CLI pass will still
leave JVM integration, actual sound, lifecycle and packaged launch unverified.

Do not add build dependencies from this experiment. The candidate coordinates,
observed from primary sources on this date, are `uk.co.caprica:vlcj:4.12.1`, its
`vlcj-natives:4.12.0` dependency, and LibVLC `3.0.24` (ARM64 and Intel distributions
exist). The release-tag POM requires Java 11; the repository currently targets
JDK 17. This establishes a plausible JVM baseline, not runtime compatibility.
The moving branch contains `4.12.2-SNAPSHOT`; do not adopt it. The vlcj development
line targets LibVLC 4 and is not the stable comparator.
[Release POM](https://raw.githubusercontent.com/caprica/vlcj/vlcj-4.12.1/pom.xml),
[stable branch](https://raw.githubusercontent.com/caprica/vlcj/vlcj-4.x/README.md),
[development line](https://github.com/caprica/vlcj),
[VideoLAN distribution index](https://get.videolan.org/vlc/last/macosx/).

## Five constraints that decide the engine

### 1. Codec and container are separate acceptance dimensions

Apple exposes runtime file-type/MIME queries and asset playability rather than
an AutPlay-specific guarantee. Treat every row below as pending for both engines;
the table is the required experiment, not a support claim.
[Apple AVFoundation overview](https://developer.apple.com/av-foundation/),
[AVURLAsset file types](https://developer.apple.com/documentation/avfoundation/avurlasset/audiovisualtypes()).

| Required media | Fixtures to supply | Particular failure to expose |
| --- | --- | --- |
| MP3 | CBR and VBR, ID3, stereo | Duration/index estimation and seek drift |
| AAC | ADTS `.aac` and LC AAC `.m4a` separately | Raw stream vs container; encoder delay |
| M4A | AAC with front and tail `moov`; ALAC if claimed | M4A alone does not identify its codec; tail metadata causes remote reads |
| FLAC | Native `.flac`, 16/24 bit; Ogg FLAC separately if claimed | Container, metadata and sample-depth differences |
| Opus | Ogg `.opus` required; WebM/CAF/MP4 only as separate optional claims | A codec identifier or Safari result does not prove AVPlayer demuxing of Ogg |
| PCM WAV | Included 48 kHz stereo/16 bit; additional mono, float, large files later | Frame alignment, large-file sizes, seek/end behavior |

Run local and authenticated streaming for each file. Supply synthetic or explicitly
authorized media of at least three seconds, preferably eight. The probe's 32 MiB
asset limit and 17-file cap keep the early experiment bounded; they are not product
limits. `--require-formats` verifies extensions exist, not codec correctness or a
complete matrix. Record codec/container/sample rate/channels/encoder identity in
the accompanying evidence. FLAC/Opus failures cannot silently become transcoding
or dropped-format policy without a separate product decision.

### 2. Bearer auth needs an explicit byte-source boundary

The probe uses the public `AVAssetResourceLoaderDelegate` API with a custom scheme,
then an ephemeral URLSession for byte requests. No undocumented
`AVURLAssetHTTPHeaderFieldsKey` dependency is introduced. Each load validates 206,
Content-Range offsets/total, Content-Length, EOF and the 32 MiB cap. No cookies,
disk cache, credential store, retry or secret-bearing URL is used. Only exactly
the original scheme/host/port may receive the bearer after redirect, at most three
times; a different loopback port is deliberately rejected before contact.
[Resource loader API](https://developer.apple.com/documentation/avfoundation/avassetresourceloaderdelegate),
[data request API](https://developer.apple.com/documentation/avfoundation/avassetresourceloadingdatarequest),
[redirect delegate API](https://developer.apple.com/documentation/foundation/urlsessiontaskdelegate/urlsession(_:task:willperformhttpredirection:newrequest:completionhandler:)).

This is a finite-file HTTP experiment, not HLS/DRM or a production streaming cache.
The loopback HTTP exception carries a generated synthetic token only. Production
needs platform-trusted HTTPS, approved origin resolution, token refresh/expiry,
revocation, 200-without-Range/416 handling, cancellation, backpressure, 64-bit large
files, range overlap, changed ETags and bounded recovery. Server data must not be
used to test these here. A token travels through an anonymous stdin pipe, never
argv/environment, logs, files, a playlist/MRL or a persisted queue. Python/Swift
strings are not guaranteed to be securely zeroed; this probe uses no real secret.

LibVLC's legacy HTTP source constructs authentication from URL username/password
and emits the request at debug level. That is not evidence for a safe bearer
header API. Check the exact selected HTTP module and log behavior before handing
it a resolved Vault URL. Prefer an application-owned authenticated byte adapter
with native read/seek callbacks if direct header/redirect control is insufficient.
Its reentrant reads, seek cancellation, lifetime and credential isolation require
their own Mac probe; they are not implemented here.
[LibVLC 3 HTTP source](https://raw.githubusercontent.com/videolan/vlc/3.0.x/modules/access/http.c).

### 3. Seek completion is not accurate or audible playback

AVPlayer supports explicit before/after tolerances and a completion callback.
The probe pauses, seeks to zero/middle/0.5 seconds before end with zero tolerances,
records actual position and rejects drift above 250 ms. It measures start to
timeline advancement, pause drift and fresh-item replacement/return. These are
early thresholds, not product SLOs. Completion/advancing time cannot prove decoded
samples reach speakers. Check output audibly on hardware and, where available,
record a loopback measurement with the known fixture. Repeated seeks while playing,
EOF next/previous, rapid command storms, duplicate queue entries and stale callbacks
after account/source changes remain real integration tests.
[AVPlayer seek API](https://developer.apple.com/documentation/avfoundation/avplayer/seek(to:tolerancebefore:toleranceafter:completionhandler:)).

### 4. Callback ownership, native loading and licenses are gate inputs

Proposed AVFoundation JVM bridge: a small Objective-C/Swift facade behind a C ABI
and JNI (or a measured JNA alternative). JVM callers submit commands to one owner;
player operations execute on the native main run loop, HTTP loader state on one
serial queue, and callbacks carry an item generation before reaching StateFlow.
Use opaque handles and explicit release/cancel, retain delegates while requests
are alive, attach native callback threads to JVM correctly, never share JNIEnv
between threads, and catch errors across the ABI. The standalone Swift CLI proves
none of that; it only prepares the underlying native experiment.
[Oracle JDK 17 JNI design](https://docs.oracle.com/en/java/javase/17/docs/specs/jni/design.html).

vlcj invokes events on native threads and warns against calling LibVLC directly
from callbacks; dispatch playback commands through its submit/executor boundary.
Retain and explicitly release player/factory/native resources. Compose UI and the
single shared state owner must not mutate from those native callbacks.
[vlcj threading and lifetime](https://raw.githubusercontent.com/caprica/vlcj/vlcj-4.x/README.md).

Package the bridge, matching architecture JDK/JNA/LibVLC libraries and required
plugins inside the app; check `otool -L`, `file`/`lipo`, relative loader paths and
plugin discovery on a clean Mac without installed JDK/VLC. ARM64 success does not
prove x64 support. Check native callbacks, load/unload and runtime requirements in
an actual packaged `.app`/`.dmg` before claiming integration. A standalone native
CLI package would not prove the Compose/JVM package.

LibVLC engine licensing and vlcj licensing differ: VideoLAN identifies the engine
as LGPLv2-or-later, while vlcj identifies itself as GPLv3-or-later and offers a
commercial alternative. Bundled plugins/codecs also need an exact license/SBOM
inventory. This spike makes no distribution license choice and buys no license.
AVFoundation is an OS framework; the bridge still requires Apple SDK/distribution
terms and package review. Review team-signed native libraries and narrowly required
JVM JIT entitlements under Hardened Runtime, not blanket disabled library validation.
[VideoLAN repository license overview](https://github.com/videolan/vlc),
[vlcj license](https://raw.githubusercontent.com/caprica/vlcj/vlcj-4.x/README.md),
[Apple Hardened Runtime](https://developer.apple.com/documentation/security/hardened-runtime),
[Apple notarization and library validation](https://developer.apple.com/videos/play/wwdc2019/703/).

### 5. Keychain and device signatures require independent compatibility proof

Use a native Keychain signer behind the existing device-proof boundary; never
export a production private key or rewrite JVM RFC8785/crypto helpers. Keychain
storage alone is not proof that a software EC key is non-exportable. Investigate
Secure Enclave P-256 where supported and explicitly decide a supported fallback
policy; locked Keychain, denied access, key loss and a changed app signing identity
must be represented as typed failures. Unattended background signing and optional
user-presence access control have different behavior after sleep/reboot.
[SecKey signing](https://developer.apple.com/documentation/security/seckeycreatesignature(_:_:_:_:)),
[external representation](https://developer.apple.com/documentation/security/seckeycopyexternalrepresentation(_:_:)),
[Secure Enclave guidance](https://developer.apple.com/documentation/security/protecting-keys-with-the-secure-enclave).

Before implementation, verify these exact wire boundaries on a disposable Mac key:

1. Existing signer signs `ASCII(domainSeparator) || SHA256(canonicalUnsignedBytes)`
   with SHA256withECDSA. The operation hashes that entire message once. With SecKey
   use `.ecdsaSignatureMessageX962SHA256` over those message bytes; do not feed the
   request hash alone or double-hash through the digest API by mistake.
2. SecKey X9.62 ECDSA output is DER; wire signatures are exactly 64-byte P1363
   `r || s`, then base64url without padding. Reuse the reviewed DER conversion,
   including sign-padding/leading-zero/length cases, and verify in the current JVM
   and server verifier. ECDSA signatures need not equal a fixture byte-for-byte.
3. EC public external representation is an X9.63 point, not Java SPKI. Produce the
   exact P-256 SubjectPublicKeyInfo bytes using the existing JVM helper boundary;
   thumbprint hashes the SPKI bytes, not the raw point. Verify in both directions.
4. Preserve domain separator newlines, UTF-8 canonical unsigned bytes, excluded
   proof fields, request SHA256 and existing Android platform fixture bytes. Do not
   masquerade a Mac as Android or alter enrollment/admission in this task.
5. Verify sign/reopen/lock/deny/delete/reinstall with a test namespace; test key
   removal must target only its own generated alias. Package-signing Developer ID
   is separate from the P-256 logical device key. Developer ID signing/notarization
   requires a later authorized release workflow; no identities are accessed here.

Read-only compatibility inputs:

- [Existing M5DeviceKeyStore and DER conversion](../../apps/android/src/main/kotlin/app/autplay/data/security/M5DeviceKeyStore.kt).
- [Self-pairing protocol domains and canonicalization](../../apps/android/src/main/kotlin/app/autplay/application/selfpairing/SelfPairingProtocol.kt).
- [Self-pairing public synthetic proof vectors](../../tests/fixtures/self-device-pairing/v1/proof-vectors.json): all five requests, canonical bytes/hash and existing proofs.
- [Profile-pairing hashes](../../tests/fixtures/profile-pairing/v1/hash-vectors.json), [schema examples](../../tests/fixtures/profile-pairing/v1/schema-examples.json).
- [Account recovery](../../tests/fixtures/account-recovery/v1/proof-vectors.json) and [deletion](../../tests/fixtures/account-deletion/v1/proof-vectors.json) domains; [discovery hashes](../../tests/fixtures/discovery/v1/hash-vectors.json).
- [ADR-020 canonicalization](../adr/ADR-020-p07-agp-kotlin-jcs-compatibility.md), [pairing security contract](../design/AutPlay_Profile_Pairing_Security_Contract_v1.md).

## Proposed ports; no shared source changes in this spike

The shared command surface is intentionally minimal:

```kotlin
interface PlaybackPort {
    val state: StateFlow<PlaybackPresentationState>
    fun play()
    fun pause()
    fun seekTo(positionMs: Long)
    fun next()
    fun previous()
}
```

This is a proposed shape, not a new compiled declaration. The coordinator should
freeze error/command semantics, negative seek clamping, flow scope, disposal and
session ownership before implementing it. Shared presentation/state and queue
policy stay common; exactly one platform media execution owner emits their facts.
Android Media3 and WorkManager remain their execution owners under
[ADR-021](../adr/ADR-021-p08-media3-room-playback-download-ownership.md).

| Additional boundary | Responsibility | Proof needed before implementation |
| --- | --- | --- |
| NativeSourceResolver | Resolve stable queue ref to an expiring in-memory source lease, authorized bytes/file handle and close/cancel capability | Correct binding/generation, revocation, redirects, range reads; token/URL absent from queue/checkpoint |
| FileChooser/FileAccess | Native chooser, selected-file capabilities/security-scoped bookmarks, bounded reads; import preserves source | Unicode, moved external disk, sandbox grant expiry, cancellation and explicit user-selected scope |
| KeychainDeviceSigner / SecretStore | Opaque key alias, ensure/public SPKI/thumbprint/sign/delete; separate session secret custody | P-256 fixtures above, access control, signed-app identity, lock/key loss; no private-key getter |
| SystemControls | MediaPlayer now-playing metadata and command registration, forwarding to PlaybackPort | Media keys/seek exactly once, main-thread callbacks, disposal, sleep/wake and stale owner |
| AppPaths | Separate database, managed media, stream cache and temporary locations | `~/Library/Application Support/AutPlay`, permissions, free-space/quota, cleanup limited to owned temp files |
| MonotonicClock | Elapsed played time independent of wall-clock changes | Define suspend/sleep accounting, wrap/units and no persisted clock samples; do not substitute wall time |
| DesktopScheduler | Durable intent recovery plus bounded in-process execution; no promise after Quit | Crash/restart, cancellation/generation, sleep/wake, atomic intent/outbox; no second download executor |

## Executable block and exact commands

All commands below run from this worktree's repository root. No Gradle is needed.
The pinned Python can be resolved with `uv python find 3.14.7`; install host tools
through the normal environment setup separately if unavailable. Every evidence
file name must be new: the runner refuses to overwrite an existing result.

Windows checks performed for this spike:

```powershell
$spikePython = (uv python find 3.14.7).Trim()
& $spikePython tools/macos-audio-spike/run_probe.py --self-check --output tools/macos-audio-spike/.runs/windows-http-003.json
& $spikePython -m py_compile tools/macos-audio-spike/fixture_server.py tools/macos-audio-spike/run_probe.py
```

The socket check proves missing-auth 401, full body 200, bounded/open/suffix 206,
invalid and multi-range 416, HEAD, correct redirect targets, detectable truncated
body, sanitized event records and closed disposable server ports. It does not prove native redirect refusal or
cancellation. Synthetic WAV SHA256:
`afad363830100469a9753b08bd5d120bcc30569bb2359f781b091263b347a3d2`.

On an actual Mac, first run the synthetic WAV smoke:

```bash
spike_python="$(uv python find 3.14.7)"
"$spike_python" tools/macos-audio-spike/run_probe.py --self-check
"$spike_python" tools/macos-audio-spike/run_probe.py --run \
  --output tools/macos-audio-spike/.runs/mac-wav-001.json
```

Then place synthetic/authorized MP3, ADTS AAC, M4A AAC, FLAC and Ogg Opus in a
flat fixture folder. If an already installed FFmpeg is used to encode the included
tone, record its version, build configuration and encoder options; do not infer
codec identity from extension or download copyrighted fixtures. Run:

```bash
"$spike_python" tools/macos-audio-spike/run_probe.py --run --require-formats \
  --fixtures tools/macos-audio-spike/.runs/fixtures \
  --output tools/macos-audio-spike/.runs/mac-formats-001.json
```

Each fixture runs local playback; auth/Range playback; same-origin redirect;
401; blocked cross-origin redirect; network truncation; native cancellation while
a response is delayed. Positive cases include pause, three seeks and replacement/
return. Negative cases require an actual item failure plus transport observations;
a timeout is never a pass. The cancel case requires AVFoundation's didCancel
callback, rather than crediting the runner's cleanup. Bounds: 25 seconds native
case, 30 seconds outer child timeout, five-second request/ten-second resource
timeout, eight active requests, 128 requests per native owner, 512 server events,
three redirects, 60-second compiler timeout. All files/sockets are disposable.
The outer runner cleans its temporary binary and media after recording hashes.
This may emit a quiet tone; CI without an audio device must be marked appropriately.

The exact build performed by the runner is:

```bash
xcrun swiftc -swift-version 5 -O -framework AVFoundation \
  -framework UniformTypeIdentifiers tools/macos-audio-spike/AudioProbe.swift \
  -o tools/macos-audio-spike/.runs/audio-probe
```

Host SDK/default deployment target are used for the experiment. UniformTypeIdentifiers
requires macOS 11+, but that is not a selected product minimum. Record real OS,
architecture, SDK, Swift/JDK versions, source/binary/media hashes, timings, failures
and missing formats. Exit 0 means the requested spike cases passed; 1 means a case
failed/incomplete required formats; 2 means no native run/toolchain. Windows
`--run` reports `MACOS_REQUIRED` and must not enter an audio evidence matrix.

## Remaining acceptance matrix and release handoff

| Gate | Current evidence | Required next result |
| --- | --- | --- |
| Portable HTTP fixture | Passed on Windows, Python 3.14.7 | Same socket check on Mac |
| AVFoundation compile and native matrix | NOT RUN | Attach sanitized per-format results and actual audio observation on ARM64 |
| Stable vlcj/LibVLC comparator | Research only | Same files/cases/seek thresholds, exact dependency+plugin hashes, safe transport |
| JVM/native boundary | NOT RUN | Pinned JDK17/Compose event loop, callback teardown and generation proof |
| Keychain P-256 interoperability | NOT RUN | Existing canonical/hash fixtures and JVM/server verification, lifecycle failures |
| Package | NOT RUN | Real `.app` and `.dmg`, native/JDK loading on a clean Mac, no incidental dependencies |
| OS/architecture claim | NOT SELECTED | Minimum OS and ARM64; x64 matrix before claiming Intel |
| Distribution | NOT DECIDED | Exact licenses/SBOM, authorized Developer ID/notarization and Gatekeeper proof |

Mac access is an execution prerequisite. Do not close stage 1 or choose the engine
until all mandatory native rows have measured results. Extending device platform
or media ownership requires the coordinator's ADR; this experiment changes neither.

# ADR-057: Desktop local library and native playback ownership

Status: Accepted for the local prototype block; production engine/release qualification remains gated.
Date: 2026-10-13.
Authorization: the user requested persistent local storage and an audio adapter for macOS.

## Decision

Desktop uses Room 3 and the existing pinned bundled SQLite driver in `shared/data`, behind
the shared `DesktopLibraryStore` contract. Its initial desktop schema is version 1 in a separate
database under `~/Library/Application Support/AutPlay`. Android schema 18, its content URI contract
and historical migration fixtures remain authoritative for Android; a Mac does not open or copy
that database. Common storage extraction can continue as the remaining account/sync modules move.
No destructive fallback, unknown-mode normalization or invented authenticated identity is allowed.

Explicit file/folder selection imports a bounded, SHA-256 verified managed copy. Originals remain
untouched. The managed locator is separate from track identity and resolves only inside the owned
media directory. Repeated identical bytes reuse an existing local import; this does not infer a
global Recording or merge uncertain catalog identities. Database mutations and local-only outbox
facts commit together. Server event materialization remains subject to ADR-018 and later consent.

One desktop application controller owns queue selection, durable entry IDs, shuffle order, repeat
and logical listening sessions. The native adapter owns media execution facts. Queue entry IDs
remain presentation media IDs; duplicate track references have distinct entries. Checkpoints and
history preserve session identity and monotonic played time. Restore is paused. Persistent queues
never contain resolved paths, URLs, credentials or native handles.

The local prototype connects AVFoundation through one packaged Swift helper and a bounded
versioned stdin/stdout protocol. File capabilities stay runtime-only; events carry a generation
to reject stale callbacks. The helper owns its native main run loop and actual AVPlayer execution;
the JVM adapter owns the child lifetime. Packaging includes the helper and JVM. Errors and missing
sources are explicit, and app shutdown checkpoints before bounded native teardown.

This connection is an experimental local adapter, not production engine acceptance. The full
codec/transport, audible hardware, Keychain, system controls, signing and clean-install gates
remain in the implementation plan. Unsupported formats cannot silently transcode or disappear.
Android Media3/WorkManager ownership and existing signed server payloads are unchanged.

## Verification after implementation

Finish the usable import/library/playlist/queue/playback block first. Then use a small acceptance
suite against real Room/SQLite, restart/checkpoint/session behavior, managed copies and actual
Mac helper/JVM playback. Verify native resources in the packaged application; preserve Android
regression gates. Fixtures, databases and media for checks are disposable and explicitly scoped.

## References

- [Local contracts](../../shared/application/src/commonMain/kotlin/app/autplay/application/desktop/DesktopLocalContracts.kt)
- [Port plan](../design/explorations/AutPlay_macOS_Port_Plan_Draft_v1.md)
- [ADR-018](ADR-018-standalone-outbox-and-journal-lineage.md)
- [ADR-021](ADR-021-p08-media3-room-playback-download-ownership.md)
- [Room KMP setup](https://developer.android.com/kotlin/multiplatform/room)
- [Compose packaged resources](https://kotlinlang.org/docs/multiplatform/compose-native-distribution.html)

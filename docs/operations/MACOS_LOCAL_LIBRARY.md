# Desktop local library and explicit file imports

This slice supplies standalone desktop persistence and managed import adapters for the
[macOS plan](../design/explorations/AutPlay_macOS_Port_Plan_Draft_v1.md). It uses the fixed
[desktop application contracts](../../shared/application/src/commonMain/kotlin/app/autplay/application/desktop/DesktopLocalContracts.kt).
It does not grant account, device, server, Vault or Android content-URI authority.

## Integration

Open the store from an application coroutine and close it before process exit:

```kotlin
val store = openDesktopLibraryStore(dataRoot)
val importer = DesktopFileImporter(store, dataRoot)
```

The factory is `suspend fun openDesktopLibraryStore(dataRoot: java.nio.file.Path): DesktopLibraryStore`
in `app.autplay.data.desktop`. The importer is `app.autplay.desktop.library.DesktopFileImporter`.
Both execute filesystem/database work on `Dispatchers.IO`; callers must not use `runBlocking`
on AWT/Compose's event thread. Observe `store.state` for committed snapshots. Restored queue
positions and sessions are persisted facts; the playback adapter owns paused restoration and
native execution, and must reject unsupported modes rather than silently replacing them.

`shared/data` is a KMP module with a JVM 17 target. Its implementation is in `jvmMain` and needs
`shared/application`, coroutines core, Room3 runtime 3.0.1 and bundled SQLite 2.7.0. KSP's JVM
processor uses the pinned Room3 compiler, and the Room3 plugin exports to `shared/data/schemas`.
The coordinator owns those build files and dependency verification. The desktop application
depends on `shared/data` and supplies the same `dataRoot` to both adapters.

The root must be an application-owned private directory, such as the application's chosen
`Library/Application Support` directory on macOS. Never select a user home as an import source
automatically. New POSIX directories have mode 0700 and files 0600; existing permissions are
not rewritten. The OS/user owns protection against concurrent malicious filesystem replacement;
the root must not be writable by untrusted processes. Symlink roots and ancestors are rejected.

## Database and state

`desktop-library-v1.db` is a separate **desktop schema v1**. The factory never opens Android's
`autplay.db`, copies its v18 database, registers Android entities or changes its migrations.
Room verifies schema identity on open; a future version, unknown identity or missing migration
fails closed with `desktop_database_open_failed`. There is no destructive migration fallback.
An exclusive OS lock on `desktop-library.lock` prevents two open factory stores for one root;
a second opener receives `desktop_store_in_use`. `close()` releases the database and lock even
when its caller is cancelled. It is idempotent.

The schema keeps:

- Track metadata, exact byte SHA-256 and size; an independent managed-locator table stores a
  basename, without an original path, content URI or account binding.
- Playlists and independently identified entries. Appending the same track twice produces two
  entry IDs; removing one preserves the other. Entry order survives restart.
- One queue's original and shuffle order, current entry ID, position, queue type, repeat mode,
  session ID/start and played time. Unknown stored mode strings survive projection unchanged.
- Listening history independent of current queue membership. A checkpoint updates the same
  event ID monotonically in observed played time; finalized history cannot be rewritten.
- Append-only local outbox intent with change ID, version, mutation kind, aggregate local ID,
  occurrence time and immutable `payloadJson`. Every domain write and its intent commit in the
  same Room transaction; queue and listening checkpoint intents commit together. Exact duplicate import and no-op
  metadata/entry removal do not invent a new domain mutation.

The v1 local JSON envelope is `version`, `mutation` and `snapshot`. Object keys are sorted
recursively and JSON is compact. Domain integer values are lossless decimal **strings**, including
timestamps, durations, byte sizes and ordinals; the envelope's bounded version is the integer 1.
Booleans and nullable fields retain JSON boolean/null types. The typed writers capture:

| Mutation | Immutable snapshot |
| --- | --- |
| Track import / metadata update | Track ID, exact title/artist/duration, SHA-256 and byte size at that mutation |
| Managed copy repair | Existing track ID, SHA-256 and byte size; no device capability |
| Playlist creation | Playlist ID and original name |
| Playlist append / removal | Entry ID, playlist ID, track ID and actual ordinal; removal captures the row before deletion |
| Queue checkpoint | Queue ID/type, ordered entry IDs/track IDs/ordinals/shuffle ordinals, current entry, position, repeat/shuffle modes and session fields |
| Listening checkpoint | Event/entry/track IDs, start, played time, position and finalized state |

Each payload is bounded to **262,144 UTF-8 bytes**. Typed writers are the mutation allowlist;
recursive safe-property and Unicode validation reject forbidden keys and lone surrogates.
`desktop_outbox_payload_too_large` or `desktop_outbox_payload_invalid` aborts the transaction
before its domain writes. Later metadata, playlist, queue or history changes never rewrite prior
intent bytes and future materialization must use the retained snapshot, not current mutable rows.

These desktop snapshots are local intent, **not authenticated Journal events**. They contain no
media locator, original path, credential, private URL or authenticated placeholder. Metadata is
private local data and is never put into routine logs. No transport, materialization, invented
server binding or modification of existing server event bytes exists. Future authenticated sync
needs a reviewed payload mapping/revalidation contract and explicit consent; it cannot assume
that the local encoding is an existing signed server wire format. These definitions amend the
initial unreleased desktop schema v1; they do not migrate an Android database.

Missing, inaccessible or unsafe media returns no runtime capability and never removes library,
playlist, queue or history rows. An explicit import of the same bytes preserves the existing
track ID and can repair its managed locator. An import verifies managed bytes again before the
store adds a row. `resolveManagedFile` validates basename, real owned containment, absence of
symlink escape, and an actual readable regular file; its absolute string is a runtime capability
for the engine, absent from state/outbox/logging. It does not rehash an entire file on every play.

This prototype publishes complete snapshots after transactions. `searchDesktopTracks(snapshot,
query, limit)` in `app.autplay.data.desktop` filters track title/artist using `Locale.ROOT`, bounds
the query to 256 characters and returns at most 1,000 results (default 200). It is suitable for
small collections. UI callers can use the same snapshot filter. It has no FTS/ranking,
pagination or large-library performance claim; snapshot refresh includes all retained history.
Queue writes are bounded to 1,000 entries and the serialized intent's byte limit; unusually long
IDs or mode strings can exceed that byte limit below the entry cap and fail atomically.
Large-library/search work requires a measured
indexed/paged read contract, rather than pretending this implementation already scales.

## Import safety and limits

Only explicit `importSelected(paths)` selections are enumerated. Folder traversal does not
follow symlinks and rejects symlink ancestors; overlapping selections share a candidate set.
Explicit unsupported files fail with a stable code; unrelated folder files are skipped.
Recognized extensions are mp3, flac, m4a, m4b, aac, wav, aif, aiff, ogg, opus and alac. This is
recognition, not a tested decoder matrix or a DRM bypass. Title falls back to the filename stem,
artist is empty and duration remains null until genuine engine metadata is available.

| Per-call limit | Bound |
| --- | --- |
| Selected paths / path length | 256 / 4,096 characters |
| Accepted files / visited entries | 2,048 / 10,000 |
| Folder depth | 16 |
| Source file / reserved source bytes | 2 GiB / 8 GiB (failed copies still consume budget) |
| Time / free-space reserve | 15 minutes / 128 MiB |
| Copy/verification buffer | 256 KiB |

The importer checks source regularity/readability and free space, streams to a unique private
temporary file, hashes and verifies the copy, then publishes `media/<sha256>.media` with an
atomic move. It verifies an existing destination instead of overwriting it. Unsupported atomic
rename fails with `import_atomic_publish_unavailable`; original files are never changed.
POSIX publication forces the file and owned directory before the DB write. Windows NIO cannot
force directory entries through this adapter; its file force does not prove power-loss durability.

A process-wide publication mutex covers destination verification and the store transaction.
The factory's OS lock prevents cooperating competing processes. Cancellation is checked during
enumeration, copy, hashing and before publication; the time bound interrupts those operations.
Once the database could have committed, verified managed bytes are retained even on a reported
failure/cancellation. A crash between publication and the transaction can leave an unreferenced
verified byte file, which a later exact-byte import reuses. It cannot produce a row pointing to
a never-published copy. Temp cleanup is best effort and never removes original or published files;
this slice has no automatic garbage collection or deletion of real user data.

`DesktopImportResult` reports completed imports, existing-byte duplicates, failures and the first
sanitized code. Storage errors use `DesktopStorageException.code`; neither adapter logs raw paths
or exposes underlying platform exception messages. A batch timeout reports `import_time_limit`;
caller cancellation propagates, so a cancelled batch can have committed earlier files.

## Verification status and next gate

Source self-review covers atomic DAO methods, duplicate-entry identity, unknown-mode projection,
filesystem validation, sanitized failures, cancellation and retained-data behavior. No Gradle or
SQLite runtime verification was run in this implementation checkout. The coordinator runs Gradle
sequentially with the repository's worker/heap settings and strict dependency verification.

Before claiming persistence acceptance, run disposable real bundled-SQLite restart/transaction
tests for tracks, duplicate playlist entries, queue/shuffle/repeat/session/history; immutable
outbox snapshots across subsequent mutations, byte-limit rejection and outbox failure rollback;
future schema rejection with retained data; missing/symlink media; duplicate/concurrent
imports; cancellation/space bounds; and source preservation. Export and retain the generated
desktop v1 schema. On macOS, verify directory force, real imports and native decoding separately.
Windows source compilation cannot establish audible Mac playback or power-loss durability.

The Room setup follows the official [Room KMP guide](https://developer.android.com/kotlin/multiplatform/room)
and [transaction API](https://developer.android.com/reference/androidx/room3/Transaction).
Resource ownership also follows coroutines' [closeable-resource cancellation guidance](https://kotlinlang.org/api/kotlinx.coroutines/kotlinx-coroutines-core/kotlinx.coroutines/coroutine-scope.html).

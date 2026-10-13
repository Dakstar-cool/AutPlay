# macOS local library acceptance

This compact suite exercises the persistent desktop library and local playback block. All
databases and media are disposable fixtures. It does not use a real home directory, existing
library, server, credentials, downloaded media or user-selected personal files.

| Case | Evidence |
| --- | --- |
| `DesktopPersistenceAcceptanceTest.restartPreservesCollectionSessionAndImmutableLocalFacts` | Real Room/BundledSQLite restart; exact-byte identity deduplication; duplicate playlist entries; queue order, shuffle, repeat and session checkpoint; idempotent history identity; imported outbox payload bytes survive metadata changes and restart; unknown queue modes survive storage. |
| `DesktopPersistenceAcceptanceTest.outboxAbortRollsBackTrackMetadataPlaylistAndQueueHistoryTogether` | A real SQLite trigger aborts outbox insertion. Track/locator, metadata, playlist creation/append/removal and queue/history mutations roll back with unchanged snapshots and outbox; reopening retains the baseline and a later mutation succeeds. |
| `DesktopPersistenceAcceptanceTest.futureDatabaseVersionIsRejectedWithoutDeletingData` | A synthetic database with future `user_version` is rejected. Rows, outbox bytes and future version remain; failure releases the store lock. |
| `DesktopImportAcceptanceTest.explicitImportPreservesOriginalDeduplicatesExactBytesAndRejectsMissingOrCorruptCopies` | Source bytes remain untouched; verified bytes are copied under a SHA-256 `.media` name; duplicates preserve identity; missing sources/capabilities fail closed; explicit reimport repairs missing bytes; corrupt published copies are never overwritten; `.part` files are cleaned. |
| `DesktopImportAcceptanceTest.symlinkSelectionsAndManagedCapabilitiesAreRejectedWithoutTouchingOriginal` | Both a selected source symlink and a managed-file symlink are rejected; original bytes remain intact. Windows may skip this case if the host cannot create symlinks. |
| `DesktopQueueAcceptanceTest.realQueueKeepsDuplicateEntriesRepeatShuffleAndRestartSessionWhileRejectingStaleEof` | Real importer/store/controller with a deterministic engine only for generation/EOF policy: selection above the 1,000-entry persistent limit returns `QUEUE_LIMIT` before changing the queue or stopping playback, a subsequent valid selection clears the error, duplicate entry identities, repeat ONE/ALL/OFF, shuffle permutation/navigation, stale error/EOF and post-EOF READY rejection, one history row per session, restored paused checkpoint and stable session. |
| `MacNativePlaybackAcceptanceTest.actualNativeTimelineQueueAndPausedRestartUseManagedNeutralExtension` | Actual `MacAudioEngine` process and AVFoundation, synthetic JVM PCM WAV copied to neutral `.media`, actual native duration/play state and advancing position, pause drift, seek, next/previous duplicates, new generations, obsolete commands that would open a missing source, actual EOF repeat, bounded close, paused native reopen with stable session/history identity, missing managed source and intact original. |

The native case uses no fake engine. The deterministic policy case proves stale callback
handling; the native case proves obsolete commands cannot replace the current generation.
Native failure facts fail the native test, rather than satisfying its playback assertions.
These cases do not prove hardware sound output, signed distribution, all codecs, UI interaction
or synchronization with a server.

## Run requirements

Use the pinned Microsoft JDK and wrapper described in [project setup](FULL_PROJECT_SETUP.md).
Keep Gradle invocations sequential, `--max-workers=1`, strict dependency verification and the
heap from `gradle.properties`. The ordinary foundation gate remains required separately.

The focused JVM tasks are:

```powershell
.\gradlew.bat --no-daemon --console=plain --max-workers=1 --dependency-verification=strict `
  -Pautplay.desktopOnly=true :shared:data:jvmTest :apps:desktop:test
```

Unsupported operating systems run the database/import/controller cases; the native case skips
unless `autplay.requireNativeTest=true`, which fails instead. Linux is not an accepted native-test host.
On macOS, absence of a configured helper, a non-executable helper, protocol/helper failure or
failure to establish actual native playback always fails the native case. No assumption skips
those failures.

For the mandatory Apple Silicon macOS gate, first package the current app using the canonical
[desktop gate](../../scripts/check-desktop.sh), then pass the **packaged executable**, not a
probe helper or fake protocol process:

```bash
bash scripts/check-desktop.sh --package-macos
task_helper="$(pwd)/apps/desktop/build/compose/binaries/main/app/AutPlay.app/Contents/app/resources/native/AutPlayAudioHelper"
test -x "$task_helper"
bash ./gradlew --no-daemon --console=plain --max-workers=1 --dependency-verification=strict \
  -Pautplay.desktopOnly=true \
  "-Pautplay.nativeTestHelper=$task_helper" -Pautplay.requireNativeTest=true \
  :shared:data:jvmTest :apps:desktop:test
```

Required gate wiring: the desktop `Test` task must forward the two Gradle properties as test-JVM
system properties `autplay.nativeTestHelper` and `autplay.requireNativeTest`. Build/CI wiring
belongs to the integration owner; adding these test files alone does not configure forwarding.
The required native case must appear as **passed**, never skipped, in the test XML.

## Isolation and evidence

Fixtures canonicalize their created temporary roots with `toRealPath()` before opening the
store/importer. This handles macOS `/var` -> `/private/var` without weakening production
symlink checks. The synthetic source files and managed copies belong to the same fixture.
Cleanup closes the controller/engine, cancels and joins the fixture scope, and closes the store
before deleting only that owned fixture tree, without following symlinks. It collects owned paths
and closes the traversal stream before deletion. Failed resource release retains the fixture and
fails the test; cleanup errors are attached as suppressed exceptions if the test already failed.
No cleanup errors are ignored and deletion is not retried. Every coroutine wait has a timeout, and JUnit adds an outer deadline;
there is no unbounded sleep or retry.

The future-version case resets `user_version` only in its synthetic database, after asserting
failed open preserved its contents. The rollback trigger exists only in its fixture database and
is dropped in `finally`. Neither mechanism is a production migration or repair procedure.

Retain `shared/data/build/test-results/jvmTest/` and `apps/desktop/build/test-results/test/` with
the exact source commit, JDK/toolchain and packaged-helper artifact identity. A Windows result
does not establish native playback acceptance. Record native helper SHA-256 and test results
from the same packaged source; do not reuse historical probe results for this acceptance claim.

This suite was authored without running Gradle in the acceptance worktree. Execution and the
sequential integration gates are owned by the integration chat.

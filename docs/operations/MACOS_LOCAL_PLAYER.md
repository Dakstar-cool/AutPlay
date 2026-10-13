# macOS local library and player

Implementation block: 2026-10-13. This is the local prototype described by
[stages 2 and 3](../design/explorations/AutPlay_macOS_Port_Plan_Draft_v1.md),
[ADR-018](../adr/ADR-018-standalone-outbox-and-journal-lineage.md) and
[ADR-021](../adr/ADR-021-p08-media3-room-playback-download-ownership.md).
The desktop-specific ownership decision is ADR-057, supplied by the integration branch.
Actual macOS audio, restart and packaging acceptance must follow integration; source review
does not establish audible playback or a qualified release.

## Using the local collection

The normal macOS entry point opens the separate desktop library under
`~/Library/Application Support/AutPlay`. Windows development uses `%LOCALAPPDATA%/AutPlay`;
Linux development uses `$XDG_DATA_HOME/AutPlay` or `~/.local/share/AutPlay`. These development
paths do not claim Windows/Linux audio support. Startup does not scan the home directory,
open an Android database or require an account/server.

Choose **Add music** in Library, then select files or folders in the system-hosted chooser.
Only selected sources reach the import adapter. It creates verified managed copies and keeps
originals intact. The UI reports imported, duplicate and failed counts; failures display
sanitized messages rather than exception text or paths. Reimporting identical bytes is handled
by the library/import adapter, not by an inferred recording identity in the UI.

Search filters titles and artists. **Play all** replaces the queue with the visible result;
clicking a track plays that track and the following visible tracks. **Enqueue** adds another
distinct queue entry even when the track already appears. Select a playlist chip in Library
before using **Add to playlist**. In Playlists, create a named list, play it, or remove individual
entries. Queue provides selection/removal, the actual persisted shuffle order and repeat modes.
History shows stable listening events and accumulated listening time.

The coral light/dark palette and English/Russian labels remain available. Theme and language
are explicitly process-local preferences. Controls and list rows support Compose focus/Tab;
Ctrl+Space requests play/pause, Ctrl+Right next and Ctrl+Left previous. These shortcuts still
pass through the application command gate. Minimum window size is 860 by 600.

## Playback ownership and restart

[DesktopPlaybackController](../../apps/desktop/src/main/kotlin/app/autplay/desktop/playback/DesktopPlaybackController.kt)
is the single queue/session policy owner implementing the shared `PlaybackPort`. UI renders
snapshots and sends stable IDs/commands. The audio engine supplies actual playback facts;
managed file capabilities remain inside desktop adapters.

- Queue entry ID is presentation `mediaId`; duplicate tracks have distinct entries.
- Shuffle order and repeat mode persist. Repeat ONE affects natural EOF; manual navigation
  follows queue order. Repeat ALL wraps. Repeat OFF stops at the boundary. Previous resets a
  currently playing/paused item to zero after three seconds, otherwise selects the prior entry.
- Commands and native events use one bounded serial actor. Stale generations and repeated EOF
  cannot advance a newly selected entry. An EOF while restored paused cannot autoplay another item.
- Shared `PlaybackCommandGate` revalidates queue type, entry identity, supported mode and source.
  Unknown persisted modes are preserved and fail closed; a user can start a new supported queue.
- Playing sessions checkpoint on a 15-second schedule and on pause, seek, error, queue/mode
  mutation and orderly close. `saveQueue` commits queue/session and its listening event together;
  selection transitions finalize the prior entry's event against that prior entry.
- Listening event ID, start time and monotonic elapsed `playedMs` survive restart. Seeking does
  not manufacture listening time. A checkpoint updates the same event instead of duplicating history.
- Normal restart restores the selected entry and position paused. Queue replacement, selection
  and completed natural playback finalize the old session. Closing pauses and checkpoints without
  finalizing the active logical session, so restart can continue it with the same identity.
- Missing sources and audio failures remain visible and retain library entries. Selecting the
  failed queue entry explicitly retries resolution/open; choosing another entry remains available.

Lists are lazy and display at most 10,000 items per view; library search can narrow the result.
Queues accept at most 1,000 entries. Playlist chips are bounded to 1,000. The chooser accepts at
most 256 selected roots. Queue and selection caps come from shared `DesktopLocalLimits`.
An oversized selection is rejected as one explicit batch-limit failure before import, without
truncating it or changing the library; an oversized queue request preserves the existing queue.
Traversal/media bounds are enforced by the import adapter. The actor
accepts at most 64 pending commands/events and reports an overloaded user-command request as a
failure rather than silently claiming completion. The native engine owns its bounded protocol.

## Native resources and lifecycle

[Main.kt](../../apps/desktop/src/main/kotlin/app/autplay/desktop/Main.kt) initializes storage,
importer and engine off the AWT event thread. The packaged helper is
`compose.application.resources.dir/native/AutPlayAudioHelper`. Local development may supply
`-Dautplay.nativeHelper=<helper-executable>`; no fake engine is substituted when it is absent.
The integration branch owns Gradle dependencies and packaging of the real helper.

Window close and process shutdown checkpoint before disposing the native engine and database.
Cleanup runs even after cancellation/checkpoint failure, with bounded waits for jobs, engine and
store. A sanitized `AUTPLAY_DESKTOP_CLOSE_FAILED` marker reports an orderly-close failure;
original files and database contents are never deleted as a recovery action.

`--smoke-test` retains the visible-window, rendered-frame and bundled-runtime checks. It must
receive an explicitly disposable root using `--test-data-root=<path>`, `--test-data-root <path>`,
or `-Dautplay.testDataRoot=<path>`. Without that override it fails before opening storage.
The ordinary library root and the home root are rejected as smoke roots. CI owns creation and
cleanup of its disposable directory. `AUTPLAY_DESKTOP_SMOKE_READY` is emitted after the window
checks and successful resource close; it does not claim audio acceptance.

## Focused acceptance after integration

Use disposable storage and synthetic/owned media, then verify real Room/SQLite and the actual
Mac JVM/helper combination. The required scenarios are:

1. Import a selected file and folder, reimport identical bytes, handle an unavailable/unsupported
   source, verify original bytes remain and counters agree with committed records.
2. Search, create a playlist, add/remove duplicate playlist entries, and play a selected result.
3. Enqueue the same track twice; verify distinct entry IDs, selection/removal and previous/next
   boundaries with OFF/ONE/ALL and persisted shuffle order.
4. Pause/seek/checkpoint, close and reopen; verify the same queue position and session/event ID,
   monotonic played time, no history duplication and no autoplay. Include a saved position at EOF.
5. Deliver an old generation's EOF/error after selection and repeat current EOF; verify exactly
   one advancement. Check native error/missing-source recovery without deleting library entries.
6. Exercise native process exit and orderly window/Quit teardown; verify resource close and a
   checkpoint or explicit sanitized failure. Check the packaged helper and disposable smoke path.
7. Check both languages/themes, long/Unicode names, resizing and keyboard focus using real UI.

No Gradle, native playback or runtime acceptance was run by this UI implementation chat. The
coordinator owns sequential pinned Gradle checks and the focused integration suite. Drag-and-drop,
system media keys, sleep/wake, account/Vault transport and signing/release qualification remain
outside this local file-chooser implementation block.

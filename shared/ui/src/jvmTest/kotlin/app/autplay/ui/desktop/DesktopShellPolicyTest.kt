package app.autplay.ui.desktop

import app.autplay.application.playback.ActiveQueueContext
import app.autplay.application.desktop.DesktopCopyInspection
import app.autplay.application.desktop.DesktopCopyInspectionLimits
import app.autplay.application.desktop.DesktopCopyInspectionState
import app.autplay.application.desktop.DesktopHistoryCursor
import app.autplay.application.desktop.DesktopHistoryLimits
import app.autplay.application.desktop.DesktopHistoryState
import app.autplay.application.desktop.DesktopImportPhase
import app.autplay.application.desktop.DesktopImportPreview
import app.autplay.application.desktop.DesktopImportPreviewFile
import app.autplay.application.desktop.DesktopImportProgress
import app.autplay.application.desktop.DesktopImportResult
import app.autplay.application.desktop.DesktopImportReviewState
import app.autplay.application.desktop.DesktopArtwork
import app.autplay.application.desktop.DesktopEmbeddedMetadata
import app.autplay.application.desktop.DesktopEmbeddedMetadataLimits
import app.autplay.application.desktop.DesktopEmbeddedTextFormat
import app.autplay.application.desktop.DesktopMetadataPreview
import app.autplay.application.desktop.DesktopMetadataState
import app.autplay.application.desktop.DesktopTrackTextBaseline
import app.autplay.application.desktop.DesktopTrackFieldProvenance
import app.autplay.application.desktop.DesktopTrackTextOrigin
import app.autplay.application.desktop.DesktopTrackTextProvenance
import app.autplay.application.desktop.DesktopTrack
import app.autplay.application.desktop.DesktopLibrarySnapshot
import app.autplay.application.desktop.DesktopPlaylist
import app.autplay.application.desktop.DesktopPlaylistEntry
import app.autplay.application.desktop.DesktopSleepTimerState
import app.autplay.application.desktop.DesktopPreferences
import app.autplay.application.desktop.DesktopPreferencesState
import app.autplay.application.desktop.DesktopSettingsTransferState
import app.autplay.application.desktop.DesktopListeningEvent
import app.autplay.application.desktop.DesktopQueue
import app.autplay.application.desktop.DesktopQueueEntry
import app.autplay.application.desktop.DesktopQueueEditTarget
import app.autplay.application.desktop.DesktopQueueEditorState
import app.autplay.application.desktop.DesktopQueueGroupAction
import app.autplay.application.desktop.planDesktopUpcomingEdit
import app.autplay.application.desktop.DesktopPlaybackTarget
import app.autplay.application.desktop.DesktopPlayerState
import app.autplay.application.desktop.DesktopTransportAction
import app.autplay.application.desktop.DesktopTastePolicy
import app.autplay.application.desktop.DesktopTasteState
import app.autplay.application.desktop.DesktopTextSearchMatches
import app.autplay.application.desktop.DesktopTextSearchPort
import app.autplay.playback.presentation.PlaybackConnectionState
import app.autplay.playback.presentation.PlaybackControlGate
import app.autplay.playback.presentation.PlaybackControlLockReason
import app.autplay.playback.presentation.PlaybackPresentationState
import app.autplay.playback.presentation.PlaybackStatus
import org.junit.Assert.assertEquals
import org.junit.Assert.assertSame
import org.junit.Assert.assertNotSame
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.util.concurrent.CancellationException
import androidx.compose.foundation.interaction.DragInteraction
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.PressInteraction
import androidx.compose.ui.geometry.Offset
import kotlinx.coroutines.runBlocking

public class DesktopShellPolicyTest {
    @Test
    public fun unavailableAdapterOrLockedStateCannotEnableAnyCommand(): Unit {
        assertSavedImportsTargets()
        assertDesktopLocalStatisticsTargets()
        assertSearchTargets()
        assertLifecycleTargets()
        assertSmoothTransitionTargets()
        assertUpcomingCommandTargets()
        assertMultiSelectionCommandTargets()
        run {
            val library = DesktopLibrarySnapshot()
            val owner = DesktopLibraryCallbacks()
            val lifetime = DesktopBrowseLifetime()
            val currentLibrary = desktopBrowseReferenceState<DesktopLibrarySnapshot?>(library)
            val currentOwner = desktopBrowseReferenceState(owner)
            val libraryGuard = desktopBrowseIdentityGuard(lifetime, library, { currentLibrary.value }) { true }
            val ownerGuard = desktopBrowseIdentityGuard(lifetime, owner, { currentOwner.value }, libraryGuard)
            val calls = mutableListOf<String>()
            val retained: (String) -> Unit = { action ->
                if (ownerGuard()) calls += action
            }
            retained("play-page")
            assertEquals(listOf("play-page"), calls)
            currentLibrary.value = null
            listOf("play-row", "inspect-shown", "remove-entry").forEach(retained)
            val equalLibrary = library.copy()
            assertEquals(library, equalLibrary)
            assertNotSame(library, equalLibrary)
            currentLibrary.value = equalLibrary
            assertSame(equalLibrary, currentLibrary.value)
            val equalLibraryGuard = desktopBrowseIdentityGuard(lifetime, equalLibrary, { currentLibrary.value }) { true }
            assertTrue(equalLibraryGuard())
            assertFalse(libraryGuard())
            retained("play-page")
            currentLibrary.value = library
            val equalOwner = owner.copy()
            assertEquals(owner, equalOwner)
            assertNotSame(owner, equalOwner)
            currentOwner.value = equalOwner
            assertSame(equalOwner, currentOwner.value)
            val equalOwnerGuard = desktopBrowseIdentityGuard(lifetime, equalOwner, { currentOwner.value }, libraryGuard)
            assertTrue(equalOwnerGuard())
            assertFalse(ownerGuard())
            retained("inspect-shown")
            val copyOwner = DesktopCopyInspectionCallbacks()
            val currentCopyOwner = desktopBrowseReferenceState(copyOwner)
            val equalCopyOwner = copyOwner.copy()
            assertEquals(copyOwner, equalCopyOwner)
            assertNotSame(copyOwner, equalCopyOwner)
            currentCopyOwner.value = equalCopyOwner
            assertSame(equalCopyOwner, currentCopyOwner.value)
            assertFalse(lifetime.matches(copyOwner, currentCopyOwner.value))
            assertTrue(lifetime.matches(equalCopyOwner, currentCopyOwner.value))
            assertFalse(desktopBrowseIdentityGuard(lifetime, equalCopyOwner, { currentCopyOwner.value }) { false }())
            currentOwner.value = owner
            lifetime.retire()
            listOf("play-row", "play-page", "inspect-shown", "remove-entry").forEach(retained)
            lifetime.retire()
            assertFalse(lifetime.isActive)
            assertFalse(libraryGuard())
            assertFalse(ownerGuard())
            assertFalse(equalLibraryGuard())
            assertFalse(equalOwnerGuard())
            assertFalse(lifetime.matches(null, null))
            assertEquals(listOf("play-page"), calls)
            val replacement = DesktopBrowseLifetime()
            assertTrue(replacement.matches(library, currentLibrary.value))
            assertFalse(lifetime.matches(library, currentLibrary.value))
        }
        run {
            val preview = DesktopImportPreview("preview-1", listOf(DesktopImportPreviewFile("file-1", "Song", 123)), 123)
            val state = DesktopImportReviewState(DesktopImportPhase.REVIEWING, preview)
            val library = DesktopLibrarySnapshot()
            val confirms = mutableListOf<String>()
            var cancels = 0
            val owner = DesktopImportReviewCallbacks(onConfirm = { confirms += it }, onCancel = { cancels++ })
            val captured = DesktopImportReviewInput(state, owner, library, DesktopSection.Home, 1, false)
            assertTrue(captured.canConfirm())
            assertTrue(captured.canCancel())
            val stale = listOf(
                DesktopImportReviewInput(state.copy(), owner, library, DesktopSection.Home, 1, false),
                DesktopImportReviewInput(state.copy(preview = preview.copy(id = "preview-2")), owner, library, DesktopSection.Home, 1, false),
                DesktopImportReviewInput(state, owner.copy(), library, DesktopSection.Home, 1, false),
                DesktopImportReviewInput(state, owner, library.copy(), DesktopSection.Home, 1, false),
                DesktopImportReviewInput(state, owner, null, DesktopSection.Home, 1, false),
                DesktopImportReviewInput(state, owner, library, DesktopSection.Library, 1, false),
                DesktopImportReviewInput(state, owner, library, DesktopSection.Home, 2, false),
                DesktopImportReviewInput(state, owner, library, DesktopSection.Home, 1, true),
                DesktopImportReviewInput(state, owner, library, DesktopSection.Home, 1, false, true),
            )
            stale.forEach { current ->
                assertFalse(DesktopImportReviewAction().confirm(captured, current, true))
                assertFalse(DesktopImportReviewAction().cancel(captured, current, true))
            }
            assertFalse(DesktopImportReviewAction().confirm(captured, captured, false))
            assertFalse(DesktopImportReviewAction().cancel(captured, captured, false))
            val retired = DesktopImportReviewAction()
            retired.retire()
            assertFalse(retired.confirm(captured, captured, true))
            assertFalse(retired.cancel(captured, captured, true))
            val once = DesktopImportReviewAction()
            assertTrue(once.confirm(captured, captured, true))
            assertTrue(once.submitted)
            assertFalse(once.confirm(captured, captured, true))
            assertFalse(once.cancel(captured, captured, true))
            assertEquals(listOf("preview-1"), confirms)
            assertEquals(0, cancels)
            val dismissed = DesktopImportReviewAction()
            assertTrue(dismissed.cancel(captured, captured, true))
            assertFalse(dismissed.cancel(captured, captured, true))
            assertFalse(dismissed.confirm(captured, captured, true))
            assertEquals(1, cancels)
            DesktopImportPhase.entries.forEach { phase ->
                val current = DesktopImportReviewInput(state.copy(phase = phase), owner, library, DesktopSection.Library, 2, false)
                assertEquals(phase == DesktopImportPhase.REVIEWING, current.canConfirm())
                assertEquals(phase != DesktopImportPhase.IDLE && phase != DesktopImportPhase.CANCELLING, current.canCancel())
                assertFalse(DesktopImportReviewAction().confirm(captured, current, true))
                assertFalse(DesktopImportReviewAction().cancel(captured, current, true))
            }
            val sticky = DesktopImportReviewInput(state, owner, library, DesktopSection.Home, 1, true)
            assertFalse(sticky.canConfirm())
            assertTrue(sticky.canCancel())
            assertTrue(DesktopImportReviewAction().cancel(sticky, sticky, true))
            val closed = DesktopImportReviewInput(state, owner, null, DesktopSection.Home, 1, false)
            assertFalse(closed.canConfirm())
            assertFalse(closed.canCancel())
            listOf("import_invalid_selection", "import_source_unreadable", "import_access_denied", "import_source_not_regular",
                "import_symlink_rejected", "import_unsupported_format", "import_empty_file", "import_file_size_limit",
                "import_batch_size_limit", "import_file_limit", "import_entry_limit", "import_depth_limit").forEach { warning ->
                val partial = preview.copy(failed = 1, errorCode = warning)
                assertTrue(desktopImportPreviewValid(partial))
                assertTrue(DesktopImportReviewInput(state.copy(preview = partial), owner, library,
                    DesktopSection.Home, 1, false).canConfirm())
            }
            listOf("import_busy", "import_library_reopen_required", "import_time_limit", "import_io_failed", "unknown_warning").forEach { fatal ->
                assertFalse(desktopImportPreviewValid(preview.copy(failed = 1, errorCode = fatal)))
            }
            listOf(preview.copy(files = emptyList(), totalBytes = 0), preview.copy(errorCode = "import_io_failed"),
                preview.copy(errorCode = "private://exception"), preview.copy(id = ""), preview.copy(failed = -1),
                preview.copy(failed = Int.MAX_VALUE), preview.copy(totalBytes = -1), preview.copy(totalBytes = Long.MAX_VALUE),
                preview.copy(files = preview.files + preview.files, totalBytes = 246),
                preview.copy(files = listOf(preview.files.single().copy(byteSize = 0)), totalBytes = 0),
                preview.copy(files = listOf(preview.files.single().copy(id = ""))),
            ).forEach { invalid ->
                val current = DesktopImportReviewInput(state.copy(preview = invalid), owner, library, DesktopSection.Home, 1, false)
                assertFalse(current.canConfirm())
                assertTrue(current.canCancel())
                assertFalse(DesktopImportReviewAction().confirm(current, current, true))
            }
            assertEquals(listOf("preview-1"), confirms)
            assertEquals(2, cancels)
        }
        run {
            val ready = readyState()
            val target = DesktopPlaybackTarget("snapshot", "entry-current", "listen-1", 7)
            val wrapper = DesktopPlayerState(ready, target)
            assertTrue(desktopTransportCommandsEnabled(null, null, false))
            assertFalse(desktopTransportCommandsEnabled(null, null, true))
            assertFalse(desktopTransportCommandsEnabled(wrapper, null, false))
            assertTrue(desktopTransportCommandsEnabled(wrapper, DesktopLibrarySnapshot(), false))
            assertFalse(desktopTransportCommandsEnabled(wrapper, DesktopLibrarySnapshot(), true))
            val captured = DesktopTransportInput(wrapper, PlaybackPresentationState(), true, true, DesktopSection.Player, 1)
            assertTrue(captured.playback === wrapper.playback)
            assertTrue(captured.controls(false).playPause)
            val disabled = DesktopPlaybackControls(false, false, false, false)
            val commands = mutableListOf<Triple<DesktopPlaybackTarget, DesktopTransportAction, Long?>>()
            var genericCalls = 0
            val callbacks = DesktopTransportCallbacks { identity, action, position -> commands += Triple(identity, action, position) }
            val legacy = DesktopPlaybackCallbacks({ genericCalls++ }, { genericCalls++ }, { genericCalls++ },
                { genericCalls++ }, { genericCalls++ })
            val refused = listOf(
                DesktopTransportInput(wrapper, ready, false, true, DesktopSection.Player, 1),
                DesktopTransportInput(wrapper, ready, true, false, DesktopSection.Player, 1),
                DesktopTransportInput(wrapper.copy(playback = ready.copy(connection = PlaybackConnectionState.Disconnected)),
                    ready, true, true, DesktopSection.Player, 1),
                DesktopTransportInput(wrapper.copy(playback = ready.copy(mediaId = null)), ready, true, true, DesktopSection.Player, 1),
                DesktopTransportInput(wrapper.copy(target = null), ready, true, true, DesktopSection.Player, 1),
                DesktopTransportInput(wrapper.copy(target = target.copy(queueId = "retired")), ready, true, true, DesktopSection.Player, 1),
                DesktopTransportInput(wrapper.copy(target = target.copy(entryId = "duplicate")), ready, true, true, DesktopSection.Player, 1),
            ) + PlaybackControlLockReason.entries.map { reason ->
                DesktopTransportInput(wrapper.copy(playback = ready.copy(controls = PlaybackControlGate.Locked(reason))),
                    ready, true, true, DesktopSection.Player, 1)
            }
            refused.forEach { input ->
                assertEquals(disabled, input.controls(false))
                assertEquals(disabled, input.controls(true))
                DesktopTransportAction.entries.forEach { action ->
                    assertFalse(dispatchDesktopTransport(input, input, action, 100, true, true, callbacks, legacy))
                    assertFalse(dispatchDesktopTransport(captured, input, action, 100, true, true, callbacks, legacy))
                }
            }
            listOf(target.copy(sessionId = "listen-2"), target.copy(generation = 8),
                target.copy(entryId = "entry-next"), target.copy(queueId = "new-queue")).forEach { retired ->
                val current = DesktopTransportInput(wrapper.copy(target = retired), ready, true, true, DesktopSection.Player, 1)
                DesktopTransportAction.entries.forEach { action ->
                    assertFalse(dispatchDesktopTransport(captured, current, action, 100, true, true, callbacks, legacy))
                }
            }
            listOf(
                DesktopTransportInput(wrapper, ready, true, true, DesktopSection.Queue, 1),
                DesktopTransportInput(wrapper, ready, true, true, DesktopSection.Player, 2),
                DesktopTransportInput(null, ready, true, true, DesktopSection.Player, 1),
            ).forEach { input ->
                assertFalse(dispatchDesktopTransport(captured, input, DesktopTransportAction.PLAY, null, true, true, callbacks, legacy))
            }
            assertFalse(dispatchDesktopTransport(captured, captured, DesktopTransportAction.PLAY, null, false, false, callbacks, legacy))
            assertFalse(dispatchDesktopTransport(captured, captured, DesktopTransportAction.PAUSE, null, true, false, callbacks, legacy))
            listOf(null, -1L, 180_001L).forEach { position ->
                assertFalse(dispatchDesktopTransport(captured, captured, DesktopTransportAction.SEEK, position, true, false, callbacks, legacy))
            }
            val missing = DesktopTransportInput(null, ready, true, true, DesktopSection.Player, 1)
            assertEquals(disabled, missing.controls(false))
            assertFalse(dispatchDesktopTransport(missing, missing, DesktopTransportAction.PLAY, null, true, false, callbacks, legacy))
            assertFalse(dispatchDesktopTransport(missing, captured, DesktopTransportAction.PLAY, null, true, true, callbacks, legacy))
            assertEquals(0, genericCalls)
            assertTrue(commands.isEmpty())
            DesktopTransportAction.entries.forEach { action ->
                val legacyMini = DesktopTransportInput(null, ready.copy(isPlaying = action == DesktopTransportAction.PAUSE),
                    true, desktopTransportCommandsEnabled(null, null, false), DesktopSection.Library, 1)
                assertTrue(dispatchDesktopTransport(legacyMini, legacyMini, action,
                    if (action == DesktopTransportAction.SEEK) 1_000 else null, true, true, callbacks, legacy))
            }
            assertEquals(5, genericCalls)
            assertTrue(commands.isEmpty())
            DesktopTransportCallbacks().onCommand(target, DesktopTransportAction.PLAY, null)
        }
        run {
            val idle = DesktopCopyInspectionState()
            assertTrue(desktopCopyCanInspect(idle, true, false, true))
            assertFalse(desktopCopyCanInspect(idle, false, false, true))
            assertFalse(desktopCopyCanInspect(idle, true, true, true))
            assertFalse(desktopCopyCanInspect(idle, true, false, false))
            listOf("desktop_snapshot_unavailable", "import_library_reopen_required").forEach { code ->
                val failed = idle.copy(errorCode = code)
                assertTrue(desktopTasteRequiresReopen(failed.errorCode))
                assertFalse(desktopCopyCanInspect(failed, true, desktopTasteRequiresReopen(failed.errorCode), true))
            }
            assertFalse(desktopTasteRequiresReopen(idle.copy(errorCode = "UNAVAILABLE").errorCode))
            val reading = idle.copy(requestId = "copy-1", busy = true, requested = 3, completed = 1)
            assertFalse(desktopCopyCanInspect(reading, true, false, true))
            assertTrue(desktopCopyCanCancel("copy-1", reading, true))
            assertTrue(desktopCopyCanCancel("copy-1", reading.copy(completed = 2), true))
            assertFalse(desktopCopyCanCancel("copy-1", reading.copy(requestId = "copy-2"), true))
            assertFalse(desktopCopyCanCancel("copy-1", reading.copy(busy = false), true))
            assertFalse(desktopCopyCanCancel(null, reading, true))
            assertFalse(desktopCopyCanCancel("copy-1", reading, false))
            val track = DesktopTrack("copy-track", "Song", "Artist", 1_000, "a".repeat(64), 100)
            val snapshot = DesktopLibrarySnapshot(tracks = listOf(track))
            var selectionCurrent = true
            val trackInput = DesktopTrackInput(snapshot, track.id, null, 1) { selectionCurrent }
            val trackView = DesktopTrackView(trackInput, track)
            assertTrue(desktopCopyCanInspect(idle, desktopTrackPreferenceCanWrite(trackInput, trackView, false), false, true))
            selectionCurrent = false
            assertFalse(desktopCopyCanInspect(idle, desktopTrackPreferenceCanWrite(trackInput, trackView, false), false, true))
            val results = mapOf(track.id to DesktopCopyInspection(track.id, track.sha256, track.byteSize, "PRESENT"))
            val input = DesktopLibraryInput(snapshot, "song", DesktopLibrarySort.Title, DesktopSortDirection.Ascending,
                DesktopPlaylistInput(snapshot, null), copyFilter = DesktopCopyAvailabilityFilter.PRESENT, copyResults = results)
            assertTrue(input.isCurrent(snapshot, "song", input.sort, input.direction, input.preferenceFilter, 0, null, 0,
                copyFilter = input.copyFilter, copyResults = results))
            assertFalse(input.isCurrent(snapshot, "song", input.sort, input.direction, input.preferenceFilter, 0, null, 0,
                copyFilter = input.copyFilter, copyResults = results.toMutableMap()))
            assertFalse(input.isCurrent(snapshot, "song", input.sort, input.direction, input.preferenceFilter, 0, null, 0,
                copyFilter = DesktopCopyAvailabilityFilter.ALL, copyResults = results))
            assertFalse(input == DesktopLibraryInput(snapshot.copy(), "song", input.sort, input.direction,
                input.playlistInput, copyFilter = input.copyFilter, copyResults = results))
            assertFalse(input == DesktopLibraryInput(snapshot, "song", input.sort, input.direction,
                input.playlistInput, viewRevision = 2, copyFilter = input.copyFilter, copyResults = results))
            assertFalse(input == DesktopLibraryInput(snapshot, "song", input.sort, input.direction,
                input.playlistInput, copyFilter = input.copyFilter, copyResults = results.toMutableMap()))
            val calls = mutableListOf<String>()
            val callbacks = DesktopCopyInspectionCallbacks(onInspect = { calls += it }, onCancel = { calls += it })
            callbacks.onInspect(listOf(track.id))
            callbacks.onCancel(requireNotNull(reading.requestId))
            assertEquals(listOf(track.id, "copy-1"), calls)
            assertEquals("ACTIVE", track.collectionState)
            assertEquals(DesktopPlaybackControls(false, false, false, false), DesktopPlaybackControls.from(
                readyState().copy(controls = PlaybackControlGate.Locked(PlaybackControlLockReason.SOURCE_UNAVAILABLE)), true))
        }
        assertMetadataAdmission()
        assertHomeCommandPolicies()
        assertHistoryRequestPolicies()
        val disabled = DesktopPlaybackControls(false, false, false, false)
        assertEquals(disabled, DesktopPlaybackControls.from(readyState(), adapterAvailable = false))
        assertEquals(
            disabled,
            DesktopPlaybackControls.from(readyState().copy(connection = PlaybackConnectionState.Disconnected), true),
        )
        assertEquals(disabled, DesktopPlaybackControls.from(readyState().copy(mediaId = null), true))
        PlaybackControlLockReason.entries.forEach { reason ->
            assertEquals(disabled, DesktopPlaybackControls.from(readyState().copy(controls = PlaybackControlGate.Locked(reason)), true))
        }
        val playlist = DesktopPlaylist("playlist", "Original", emptyList())
        val snapshot = DesktopLibrarySnapshot(playlists = listOf(playlist))
        val input = DesktopPlaylistInput(snapshot, playlist.id, 1)
        assertTrue(input.isCurrent(snapshot, playlist.id, 1))
        assertFalse(input.isCurrent(snapshot.copy(), playlist.id, 1))
        assertFalse(input.isCurrent(snapshot, "other", 1))
        assertFalse(input.isCurrent(snapshot, playlist.id, 3))
        run {
            val paged = DesktopPlaylistInput(snapshot, playlist.id, 1, "query", 2, 3, 4)
            val same = DesktopPlaylistInput(snapshot, playlist.id, 1, "query", 2, 3, 4)
            assertEquals(paged, same)
            assertEquals(paged.hashCode(), same.hashCode())
            val retired = listOf(
                DesktopPlaylistInput(snapshot.copy(), playlist.id, 1, "query", 2, 3, 4),
                DesktopPlaylistInput(snapshot, "other", 1, "query", 2, 3, 4),
                DesktopPlaylistInput(snapshot, playlist.id, 3, "query", 2, 3, 4),
                DesktopPlaylistInput(snapshot, playlist.id, 1, "changed", 2, 3, 4),
                DesktopPlaylistInput(snapshot, playlist.id, 1, "query", 3, 3, 4),
                DesktopPlaylistInput(snapshot, playlist.id, 1, "query", 2, 4, 4),
                // Returning to the same pages still retires the original callback.
                DesktopPlaylistInput(snapshot, playlist.id, 1, "query", 2, 3, 6),
            )
            retired.forEach { current ->
                assertFalse(paged == current)
                assertFalse(paged.isCurrent(current.library, current.selectedId, current.selectionRevision,
                    current.query, current.choicePage, current.entryPage, current.browseRevision))
            }
            assertTrue(paged.isCurrent(snapshot, playlist.id, 1, "query", 2, 3, 4))
            val libraryInput = DesktopLibraryInput(snapshot, "song", DesktopLibrarySort.Title,
                DesktopSortDirection.Ascending, paged, page = 2, browseRevision = 4)
            assertTrue(libraryInput.isCurrent(snapshot, "song", DesktopLibrarySort.Title,
                DesktopSortDirection.Ascending, DesktopTrackPreferenceFilter.ALL, 0, playlist.id, 1,
                page = 2, browseRevision = 4))
            assertFalse(libraryInput.isCurrent(snapshot, "song", DesktopLibrarySort.Title,
                DesktopSortDirection.Ascending, DesktopTrackPreferenceFilter.ALL, 0, playlist.id, 1,
                page = 3, browseRevision = 4))
            assertFalse(libraryInput.isCurrent(snapshot, "song", DesktopLibrarySort.Title,
                DesktopSortDirection.Ascending, DesktopTrackPreferenceFilter.ALL, 0, playlist.id, 1,
                page = 2, browseRevision = 6))
            assertFalse(libraryInput == DesktopLibraryInput(snapshot, "song", DesktopLibrarySort.Title,
                DesktopSortDirection.Ascending, paged, page = 3, browseRevision = 4))
            assertFalse(libraryInput == DesktopLibraryInput(snapshot, "song", DesktopLibrarySort.Title,
                DesktopSortDirection.Ascending, paged, page = 2, browseRevision = 6))
            assertFalse(libraryInput == DesktopLibraryInput(snapshot, "song", DesktopLibrarySort.Title,
                DesktopSortDirection.Ascending, retired.last(), page = 2, browseRevision = 4))
        }
        val draft = DesktopPlaylistRenameDraft(playlist.id, playlist.name, "  Renamed  ")
        assertTrue(draft.canSubmit(playlist.copy(entries = listOf(DesktopPlaylistEntry("new", "track")))))
        assertEquals("  Renamed  ", draft.name)
        assertFalse(draft.canSubmit(playlist.copy(id = "other")))
        assertFalse(draft.canSubmit(playlist.copy(name = "External rename")))
        assertFalse(draft.canSubmit(null))
        assertFalse(DesktopPlaylistRenameDraft(playlist.id, playlist.name).canSubmit(playlist))
        assertFalse(DesktopPlaylistRenameDraft(playlist.id, playlist.name, " ").canSubmit(playlist))
        assertTrue(validDesktopPlaylistName("a".repeat(4094) + "\uD83C\uDFB5"))
        listOf("", "\t ", "a".repeat(4097), "a\u0000", "a\uD800", "\uDC00a",
            "a".repeat(4095) + "\uD83C\uDFB5").forEach { assertFalse(validDesktopPlaylistName(it)) }
        assertTrue(validDesktopPlaylistDescription(null))
        assertEquals(null, desktopPlaylistDescriptionValue(""))
        listOf(" ", "\t\r\n", "  First\nSecond  ", "<a href='local://text'>plain text</a>",
            "a".repeat(500), "a".repeat(498) + "\uD83C\uDFB5", "\uD83C\uDFB5".repeat(250)).forEach { exact ->
            assertTrue(validDesktopPlaylistDescription(exact))
            assertEquals(exact, desktopPlaylistDescriptionValue(exact))
        }
        listOf("", "a".repeat(501), "a".repeat(499) + "\uD83C\uDFB5", "\uD83C\uDFB5".repeat(251),
            "\u0000", "a\u0000b", "a\uD800", "\uDC00a", "\uD800\uD800", "\uDC00\uD800").forEach {
            assertFalse(validDesktopPlaylistDescription(it))
        }
        val described = playlist.copy(description = "  Saved\ntext  ")
        val descriptionDraft = DesktopPlaylistDescriptionDraft(described.id, described.description, "  New\ntext  ")
        assertEquals("  New\ntext  ", descriptionDraft.description)
        assertTrue(descriptionDraft.canSubmit(described.copy(name = "Renamed", entries = listOf(DesktopPlaylistEntry("new", "track")))))
        assertFalse(descriptionDraft.canSubmit(described.copy(description = "External edit")))
        assertFalse(descriptionDraft.canSubmit(described.copy(id = "other")))
        assertFalse(descriptionDraft.canSubmit(null))
        assertFalse(DesktopPlaylistDescriptionDraft(described.id, described.description).canSubmit(described))
        assertTrue(DesktopPlaylistDescriptionDraft(described.id, described.description, "").canSubmit(described))
        assertFalse(DesktopPlaylistDescriptionDraft(playlist.id, null, "").canSubmit(playlist))
        // A retained legacy empty value is distinct from null in the raw compare-and-set baseline.
        val legacyEmpty = playlist.copy(description = "")
        assertTrue(DesktopPlaylistDescriptionDraft(playlist.id, "").canSubmit(legacyEmpty))
        assertFalse(DesktopPlaylistDescriptionDraft(playlist.id, "").matches(playlist))
        listOf("x".repeat(501), "\u0000legacy", "\uD800legacy").forEach { legacy ->
            val retained = playlist.copy(description = legacy)
            val legacyDraft = DesktopPlaylistDescriptionDraft(retained.id, retained.description)
            assertEquals(legacy, legacyDraft.text)
            assertTrue(legacyDraft.matches(retained))
            assertFalse(legacyDraft.canSubmit(retained))
            assertTrue(legacyDraft.withText("").canSubmit(retained))
            assertTrue(legacyDraft.withText("Replacement").canSubmit(retained))
            assertEquals(legacy, retained.copy(name = "Unrelated rename").description)
        }
        val track = DesktopTrack("track", "Original", "Artist", 1_000, "a".repeat(64), 1)
        val textDraft = DesktopTrackTextDraft(track.id, track.title, track.artist, "  Edited  ", "")
        assertTrue(textDraft.canSubmit(track.copy(durationMs = 2_000)))
        assertEquals("  Edited  ", textDraft.title)
        assertEquals("", textDraft.artist)
        assertFalse(textDraft.canSubmit(track.copy(title = "External edit")))
        assertFalse(textDraft.canSubmit(track.copy(artist = "External artist")))
        assertFalse(textDraft.canSubmit(track.copy(id = "other")))
        assertFalse(textDraft.canSubmit(null))
        assertFalse(DesktopTrackTextDraft(track.id, track.title, track.artist).canSubmit(track))
        assertFalse(DesktopTrackTextDraft(track.id, track.title, track.artist, " ", "").canSubmit(track))
        assertFalse(DesktopTrackTextDraft(track.id, track.title, track.artist, "Edited", "a\u0000").canSubmit(track))
        assertTrue(validDesktopTrackText("a".repeat(4094) + "\uD83C\uDFB5", title = true))
        assertTrue(validDesktopTrackText(" \t", title = false))
        listOf("a".repeat(4097), "a\u0000", "a\uD800", "\uDC00a", "a".repeat(4095) + "\uD83C\uDFB5").forEach {
            assertFalse(validDesktopTrackText(it, title = true))
            assertFalse(validDesktopTrackText(it, title = false))
        }
        listOf("", " \t").forEach { assertFalse(validDesktopTrackText(it, title = true)) }
        val target = DesktopPlaylistDeleteTarget(playlist.id, playlist.name, 0)
        assertTrue(target.matches(playlist.copy()))
        assertFalse(target.matches(playlist.copy(name = "Renamed")))
        assertFalse(target.matches(playlist.copy(entries = listOf(DesktopPlaylistEntry("new", track.id)))))
        assertFalse(target.matches(playlist.copy(id = "other")))
        assertFalse(target.matches(null))
        var selectedTrackId: String? = track.id
        var revision = 1L
        val trackInput = DesktopTrackInput(snapshot, track.id, null, 1) { selectedTrackId == track.id && revision == 1L }
        assertTrue(trackInput.isSelectionCurrent())
        assertTrue(trackInput == DesktopTrackInput(snapshot, track.id, null, 1))
        assertFalse(trackInput == DesktopTrackInput(snapshot.copy(), track.id, null, 1))
        assertFalse(trackInput == DesktopTrackInput(snapshot, track.id, null, 3))
        selectedTrackId = "other"
        assertFalse(trackInput.isSelectionCurrent())
        selectedTrackId = track.id
        revision = 3
        assertFalse(trackInput.isSelectionCurrent())

        val preferenceSnapshot = DesktopLibrarySnapshot(tracks = listOf(track))
        val preferenceInput = DesktopTrackInput(preferenceSnapshot, track.id, null, 3) {
            selectedTrackId == track.id && revision == 3L
        }
        val preferenceView = DesktopTrackView(preferenceInput, track)
        // Metadata is writable with no current playback ID or native connection.
        assertTrue(desktopTrackPreferenceCanWrite(preferenceInput, preferenceView, false))
        assertFalse(desktopTrackPreferenceCanWrite(preferenceInput, preferenceView, true))
        assertFalse(desktopTrackPreferenceCanWrite(preferenceInput, DesktopTrackView(), false))
        assertFalse(desktopTrackPreferenceCanWrite(preferenceInput, DesktopTrackView(preferenceInput), false))
        assertFalse(desktopTrackPreferenceCanWrite(preferenceInput,
            DesktopTrackView(preferenceInput, track.copy(id = "other")), false))
        assertFalse(desktopTrackPreferenceCanWrite(DesktopTrackInput(preferenceSnapshot.copy(), track.id, null, 3), preferenceView, false))
        val noLibrary = DesktopTrackInput(null, track.id, null)
        assertFalse(desktopTrackPreferenceCanWrite(noLibrary, DesktopTrackView(noLibrary, track), false))
        selectedTrackId = "other"
        assertFalse(desktopTrackPreferenceCanWrite(preferenceInput, preferenceView, false))
        selectedTrackId = track.id
        revision = 5
        assertFalse(desktopTrackPreferenceCanWrite(preferenceInput, preferenceView, false))

        val filterInput = DesktopLibraryInput(preferenceSnapshot, "song", DesktopLibrarySort.Title,
            DesktopSortDirection.Ascending, DesktopPlaylistInput(preferenceSnapshot, null, 1),
            DesktopTrackPreferenceFilter.LIKED, 7)
        assertTrue(filterInput.isCurrent(preferenceSnapshot, "song", DesktopLibrarySort.Title,
            DesktopSortDirection.Ascending, DesktopTrackPreferenceFilter.LIKED, 7, null, 1))
        assertFalse(filterInput.isCurrent(preferenceSnapshot.copy(), "song", DesktopLibrarySort.Title,
            DesktopSortDirection.Ascending, DesktopTrackPreferenceFilter.LIKED, 7, null, 1))
        assertFalse(filterInput.isCurrent(preferenceSnapshot, "other", DesktopLibrarySort.Title,
            DesktopSortDirection.Ascending, DesktopTrackPreferenceFilter.LIKED, 7, null, 1))
        assertFalse(filterInput.isCurrent(preferenceSnapshot, "song", DesktopLibrarySort.Artist,
            DesktopSortDirection.Ascending, DesktopTrackPreferenceFilter.LIKED, 7, null, 1))
        assertFalse(filterInput.isCurrent(preferenceSnapshot, "song", DesktopLibrarySort.Title,
            DesktopSortDirection.Descending, DesktopTrackPreferenceFilter.LIKED, 7, null, 1))
        assertFalse(filterInput.isCurrent(preferenceSnapshot, "song", DesktopLibrarySort.Title,
            DesktopSortDirection.Ascending, DesktopTrackPreferenceFilter.ALL, 7, null, 1))
        // A/B/A filter/query/sort closures are still stale after returning to identical values.
        assertFalse(filterInput.isCurrent(preferenceSnapshot, "song", DesktopLibrarySort.Title,
            DesktopSortDirection.Ascending, DesktopTrackPreferenceFilter.LIKED, 9, null, 1))
        assertFalse(filterInput.isCurrent(preferenceSnapshot, "song", DesktopLibrarySort.Title,
            DesktopSortDirection.Ascending, DesktopTrackPreferenceFilter.LIKED, 7, "other", 1))
        assertFalse(filterInput.isCurrent(preferenceSnapshot, "song", DesktopLibrarySort.Title,
            DesktopSortDirection.Ascending, DesktopTrackPreferenceFilter.LIKED, 7, null, 3))
        assertTrue(filterInput == DesktopLibraryInput(preferenceSnapshot, "song", DesktopLibrarySort.Title,
            DesktopSortDirection.Ascending, DesktopPlaylistInput(preferenceSnapshot, null, 1),
            DesktopTrackPreferenceFilter.LIKED, 7))
        assertFalse(filterInput == DesktopLibraryInput(preferenceSnapshot, "song", DesktopLibrarySort.Title,
            DesktopSortDirection.Ascending, DesktopPlaylistInput(preferenceSnapshot, null, 1),
            DesktopTrackPreferenceFilter.ALL, 7))
        assertFalse(filterInput == DesktopLibraryInput(preferenceSnapshot, "song", DesktopLibrarySort.Title,
            DesktopSortDirection.Ascending, DesktopPlaylistInput(preferenceSnapshot, null, 1),
            DesktopTrackPreferenceFilter.LIKED, 9))

        val timer = DesktopSleepTimerState("queue", "entry", "session", "plan", 60_000, commandsEnabled = true)
        val timerTarget = DesktopSleepTimerTarget(timer)
        assertTrue(timerTarget.canArm(timer, reopenRequired = false))
        assertFalse(timerTarget.canArm(timer, reopenRequired = true))
        assertFalse(timerTarget.canArm(timer.copy(commandsEnabled = false), reopenRequired = false))
        assertTrue(timerTarget.canCancel(timer.copy(commandsEnabled = false)))
        assertTrue(timerTarget.matches(timer.copy(remainingMs = 59_000)))
        listOf(timer.copy(queueId = "other"), timer.copy(currentEntryId = "other"),
            timer.copy(sessionId = "other"), timer.copy(planId = "new-plan"), timer.copy(planId = null)).forEach {
            assertFalse(timerTarget.matches(it))
            assertFalse(timerTarget.canArm(it, reopenRequired = false))
            assertFalse(timerTarget.canCancel(it))
        }
        listOf(timer.copy(queueId = null), timer.copy(currentEntryId = null), timer.copy(sessionId = null)).forEach {
            assertFalse(DesktopSleepTimerTarget(it).canArm(it, reopenRequired = false))
        }
        val noPlan = timer.copy(planId = null, remainingMs = null)
        assertTrue(DesktopSleepTimerTarget(noPlan).canArm(noPlan, reopenRequired = false))
        assertFalse(DesktopSleepTimerTarget(noPlan).canCancel(noPlan))

        val preferences = DesktopPreferencesState()
        val preview = preferences.value.copy(appLanguage = "RU", accentPalette = "BLUE")
        val transfer = DesktopSettingsTransferState(previewId = "preview-1", preview = preview)
        val previewTarget = DesktopSettingsPreviewTarget("preview-1", preview)
        assertTrue(previewTarget.canConfirm(preferences, transfer))
        assertTrue(previewTarget.canDismiss(transfer))
        assertTrue(desktopSettingsCanWrite(null, DesktopSettingsTransferState()))
        assertFalse(previewTarget.canConfirm(null, transfer))
        assertFalse(previewTarget.canConfirm(preferences.copy(writable = false), transfer))
        assertFalse(desktopSettingsCanWrite(preferences.copy(writable = false), transfer))
        assertFalse(previewTarget.canConfirm(preferences, transfer.copy(busy = true)))
        assertFalse(previewTarget.canDismiss(transfer.copy(busy = true)))
        assertFalse(desktopSettingsCanWrite(preferences, transfer.copy(busy = true)))
        listOf(transfer.copy(previewId = "preview-2"), transfer.copy(preview = preview.copy()),
            transfer.copy(previewId = null), transfer.copy(preview = null), DesktopSettingsTransferState()).forEach {
            assertFalse(previewTarget.matches(it))
            assertFalse(previewTarget.canConfirm(preferences, it))
            assertFalse(previewTarget.canDismiss(it))
        }
        assertTrue(previewTarget.matches(transfer.copy(messageCode = "desktop_settings_saved")))
        assertTrue(previewTarget.canConfirm(preferences, transfer.copy(errorCode = "desktop_settings_save_failed")))
        assertTrue(previewTarget.canDismiss(transfer.copy(errorCode = "desktop_settings_save_failed")))
        assertEquals(DesktopText.SettingsTransferFailed, desktopSettingsErrorText("private://unknown/secret"))
        assertEquals(DesktopText.SettingsTransferFailed, desktopSettingsMessageText("unknown_success"))
        assertEquals(DesktopText.SettingsReadOnly, desktopSettingsErrorText("desktop_settings_read_only"))
        assertEquals(DesktopText.SettingsReopenRequired, desktopSettingsErrorText("desktop_settings_reopen_required"))
        assertEquals(DesktopText.SettingsExportTargetExists, desktopSettingsErrorText("desktop_settings_export_target_exists"))
        assertCollectionConfirmation()
        assertTasteCommandTargets()
    }

    @Test
    public fun connectedStateRespectsNavigationAndSeekAvailability(): Unit {
        assertSavedImportsPreparation()
        assertSearchPreparation()
        assertLifecycleHandlers()
        assertTrackVisualTransitions()
        assertUpcomingPolicies()
        assertMultiSelectionPolicies()
        run {
            val files = List(DESKTOP_IMPORT_PREVIEW_LIMIT) { DesktopImportPreviewFile("file-$it", "Song $it", 123) }
            val preview = DesktopImportPreview("all-files", files, 123L * files.size)
            assertTrue(desktopImportPreviewValid(preview))
            val shown = mutableListOf<String>()
            repeat(21) { requested ->
                val page = desktopImportPage(files.size, requested)
                assertEquals(requested, page.index)
                assertEquals(21, page.pages)
                assertEquals(2_048, page.total)
                assertTrue(page.end - page.first <= 100)
                shown += files.subList(page.first, page.end).map { it.id }
            }
            assertEquals(files.map { it.id }, shown)
            assertEquals(48, desktopImportPage(files.size, 20).let { it.end - it.first })
            assertEquals(0, desktopImportPage(files.size, Int.MIN_VALUE).index)
            assertEquals(20, desktopImportPage(files.size, Int.MAX_VALUE).index)
            assertEquals(DesktopImportPage(0, 1, 0, 0, 0), desktopImportPage(-1, -1))
            assertEquals(DesktopImportPage(0, 1, 0, 100, 100), desktopImportPage(100, 1))
            assertEquals(DesktopImportPage(1, 2, 100, 101, 101), desktopImportPage(101, 1))
            val foreign = desktopImportPage(Int.MAX_VALUE, Int.MAX_VALUE)
            assertEquals(Int.MAX_VALUE, foreign.total)
            assertEquals(2_048, foreign.end)
            assertEquals(21, foreign.pages)
            assertFalse(desktopImportPreviewValid(preview.copy(files = files + files.first().copy(id = "too-many"))))
            assertFalse(desktopImportPreviewValid(preview.copy(files = listOf(files.first().copy(byteSize = Long.MAX_VALUE)))))
            val maxFileBytes = 2L * 1024 * 1024 * 1024
            val batch = List(4) { DesktopImportPreviewFile("large-$it", "Song $it", maxFileBytes) }
            assertTrue(desktopImportPreviewValid(preview.copy(files = batch, totalBytes = 4 * maxFileBytes)))
            assertFalse(desktopImportPreviewValid(preview.copy(files = batch + batch.first().copy(id = "over-budget"),
                totalBytes = 5 * maxFileBytes)))
            val confirms = mutableListOf<String>()
            val callbacks = DesktopImportReviewCallbacks(onConfirm = { confirms += it })
            val input = DesktopImportReviewInput(DesktopImportReviewState(DesktopImportPhase.REVIEWING, preview),
                callbacks, DesktopLibrarySnapshot(), DesktopSection.Library, 5, false)
            assertTrue(DesktopImportReviewAction().confirm(input, input, true))
            assertEquals(listOf("all-files"), confirms)
        }
        run {
            val track = DesktopTrack("duplicate", "Same title", "Artist", 180_000, "a".repeat(64), 100)
            val other = track.copy(id = "other", title = "Same title")
            val entries = listOf(DesktopQueueEntry("entry-current", track.id), DesktopQueueEntry("entry-next", track.id),
                DesktopQueueEntry("entry-previous", other.id))
            val queue = DesktopQueue("snapshot", entries, "entry-current", shuffleEnabled = true,
                shuffleOrder = listOf("entry-previous", "entry-current", "entry-next"), sessionId = "listen-1")
            val library = DesktopLibrarySnapshot(tracks = List(10_001) { index -> track.copy(id = "unrelated-$index") } +
                listOf(track, other), queue = queue)
            val ready = readyState().copy(localTrackRefId = track.id)
            val target = DesktopPlaybackTarget(queue.id, "entry-current", "listen-1", 7)
            val wrapper = DesktopPlayerState(ready, target)
            val input = DesktopPlayerInput(library, ready, target, 1)
            var checkpoints = 0
            val prepared = prepareDesktopPlayerView(input) { checkpoints++ }
            assertTrue(checkpoints > 40)
            assertEquals("entry-previous", prepared.previous?.entryId)
            assertEquals(other.id, prepared.previous?.track?.id)
            assertEquals("entry-current", prepared.current?.entryId)
            assertEquals("entry-next", prepared.next?.entryId)
            assertEquals(track.id, prepared.current?.track?.id)
            assertEquals(track.id, prepared.next?.track?.id)
            assertTrue(desktopPlayerViewIsCurrent(input, prepared, true))
            assertTrue(desktopPlayerViewIsCurrent(DesktopPlayerInput(library, ready.copy(positionMs = 999), target, 1), prepared, true))
            assertFalse(desktopPlayerViewIsCurrent(input, prepared, false))
            listOf(DesktopPlayerInput(library.copy(), ready, target, 1), DesktopPlayerInput(library, ready, target, 2),
                DesktopPlayerInput(library, ready, target.copy(generation = 8), 1),
                DesktopPlayerInput(library, ready.copy(nextMediaId = null), target, 1)).forEach { retired ->
                assertFalse(desktopPlayerViewIsCurrent(retired, prepared, true))
            }
            assertEquals(null, prepareDesktopPlayerView(DesktopPlayerInput(null, ready, target, 1)).current)
            assertEquals(null, prepareDesktopPlayerView(DesktopPlayerInput(DesktopLibrarySnapshot(), ready, target, 1)).current)
            assertEquals(null, prepareDesktopPlayerView(DesktopPlayerInput(library,
                ready.copy(context = ActiveQueueContext.Absent), target, 1)).current)
            assertEquals(null, prepareDesktopPlayerView(DesktopPlayerInput(library,
                ready.copy(mediaId = "missing"), target, 1)).current)
            val absentEntry = prepareDesktopPlayerView(DesktopPlayerInput(library.copy(queue = queue.copy(
                entries = entries.filterNot { it.id == "entry-current" })), ready.copy(localTrackRefId = null), target, 1))
            assertEquals(null, absentEntry.current)
            assertEquals(null, absentEntry.previous)
            assertEquals(null, absentEntry.next)
            val missingTrack = prepareDesktopPlayerView(DesktopPlayerInput(library.copy(tracks = emptyList()), ready, target, 1))
            assertEquals("entry-current", missingTrack.current?.entryId)
            assertEquals(null, missingTrack.current?.track)
            val noNeighbors = prepareDesktopPlayerView(DesktopPlayerInput(library,
                ready.copy(previousMediaId = null, nextMediaId = null), target, 1))
            assertEquals(null, noNeighbors.previous)
            assertEquals(null, noNeighbors.next)
            // Repeating a single actor-selected occurrence retains its entry identity in every slot.
            val repeat = prepareDesktopPlayerView(DesktopPlayerInput(library,
                ready.copy(previousMediaId = "entry-current", nextMediaId = "entry-current"), target, 1))
            assertEquals("entry-current", repeat.previous?.entryId)
            assertEquals("entry-current", repeat.next?.entryId)
            val maximum = entries + List(997) { DesktopQueueEntry("bounded-$it", track.id) }
            assertEquals("entry-current", prepareDesktopPlayerView(DesktopPlayerInput(
                library.copy(queue = queue.copy(entries = maximum)), ready, target, 1)).current?.entryId)
            assertEquals(null, prepareDesktopPlayerView(DesktopPlayerInput(library.copy(queue = queue.copy(
                entries = maximum + DesktopQueueEntry("overflow", track.id))), ready, target, 1)).current)
            assertEquals(null, prepareDesktopPlayerView(DesktopPlayerInput(library.copy(queue = queue.copy(
                entries = entries + entries.first())), ready, target, 1)).current)
            var cancellationCheckpoints = 0
            try {
                prepareDesktopPlayerView(input) { if (++cancellationCheckpoints == 5) throw CancellationException("player_cancelled") }
                fail("Player preparation must propagate cancellation")
            } catch (expected: CancellationException) {
                assertEquals("player_cancelled", expected.message)
            }
            assertEquals(entries, library.queue?.entries)
            assertEquals(listOf("entry-previous", "entry-current", "entry-next"), library.queue?.shuffleOrder)
            val full = DesktopTransportInput(wrapper, ready, true, true, DesktopSection.Player, 1)
            val draft = DesktopSeekDraft(full, 180_000, 0.25f)
            val lifetime = DesktopSeekLifetime(full, false)
            assertEquals(lifetime, DesktopSeekLifetime(DesktopTransportInput(wrapper.copy(playback = ready.copy(positionMs = 99)),
                ready, true, true, DesktopSection.Player, 1), false))
            val gesture = DesktopSeekGesture()
            assertEquals(45_000L, gesture.change(full, full, 0.25f, true, false)?.positionMs())
            // A real Cancel may omit Finished; it must release the draft for the next gesture.
            gesture.cancel()
            assertEquals(null, gesture.finish(full, true, false))
            assertEquals(90_000L, gesture.change(full, full, 0.5f, true, false)?.positionMs())
            assertEquals(90_000L, gesture.finish(full, true, false)?.positionMs())
            assertEquals(null, gesture.finish(full, true, false))
            // Cancel reaches Finished before any buffered interaction observer can run.
            var cancelledInteractions = 0
            val source = DesktopSeekInteractionSource(MutableInteractionSource()) {
                gesture.cancel()
                cancelledInteractions++
            }
            val drag = DragInteraction.Start()
            gesture.change(full, full, 0.25f, true, false)
            assertTrue(source.tryEmit(DragInteraction.Cancel(drag)))
            assertEquals(1, cancelledInteractions)
            assertEquals(null, gesture.finish(full, true, false))
            gesture.change(full, full, 0.5f, true, false)
            runBlocking { source.emit(DragInteraction.Cancel(drag)) }
            assertEquals(2, cancelledInteractions)
            assertEquals(null, gesture.finish(full, true, false))
            // Stop and press cancellation still permit a normal drag completion.
            gesture.change(full, full, 0.5f, true, false)
            source.tryEmit(DragInteraction.Stop(drag))
            source.tryEmit(PressInteraction.Cancel(PressInteraction.Press(Offset.Zero)))
            assertEquals(2, cancelledInteractions)
            assertEquals(90_000L, gesture.finish(full, true, false)?.positionMs())
            val rejecting = object : MutableInteractionSource by MutableInteractionSource() {
                override fun tryEmit(interaction: androidx.compose.foundation.interaction.Interaction): Boolean = false
            }
            val rejectedCancel = DesktopSeekInteractionSource(rejecting) { gesture.cancel() }
            gesture.change(full, full, 0.25f, true, false)
            assertFalse(rejectedCancel.tryEmit(DragInteraction.Cancel(drag)))
            assertEquals(null, gesture.finish(full, true, false))
            val replacement = DesktopTransportInput(wrapper.copy(target = target.copy(generation = 8)),
                ready, true, true, DesktopSection.Player, 1)
            assertEquals(45_000L, gesture.change(full, full, 0.25f, true, false)?.positionMs())
            assertEquals(target, gesture.change(full, replacement, 0.75f, true, false)?.input?.target)
            assertEquals(null, gesture.finish(replacement, true, false))
            gesture.retire()
            assertEquals(null, gesture.change(full, full, 0.75f, true, false))
            assertEquals(null, gesture.finish(full, true, false))
            val freshGesture = DesktopSeekGesture()
            assertEquals(replacement.target, freshGesture.change(replacement, replacement, 0.5f, true, false)?.input?.target)
            assertEquals(replacement.target, freshGesture.finish(replacement, true, false)?.input?.target)
            val disabled = DesktopTransportInput(wrapper, ready, true, false, DesktopSection.Player, 1)
            assertFalse(lifetime == DesktopSeekLifetime(disabled, false))
            assertFalse(lifetime == DesktopSeekLifetime(replacement, false))
            val lostGate = DesktopSeekGesture()
            lostGate.change(full, full, 0.25f, true, false)
            lostGate.retire()
            assertEquals(null, lostGate.finish(full, true, false))
            assertEquals(null, lostGate.change(full, full, 0.5f, true, false))
            assertEquals(90_000L, DesktopSeekGesture().change(full, full, 0.5f, true, false)?.positionMs())
            assertEquals(45_000L, draft.positionMs())
            assertEquals(90_000L, draft.withFraction(0.5f).positionMs())
            assertTrue(draft.matches(full, true, false))
            assertTrue(draft.matches(DesktopTransportInput(wrapper.copy(playback = ready.copy(positionMs = 120_000)),
                ready, true, true, DesktopSection.Player, 1), true, false))
            listOf(wrapper.copy(target = target.copy(sessionId = "listen-2")),
                wrapper.copy(target = target.copy(generation = 8)),
                wrapper.copy(target = null),
                wrapper.copy(playback = ready.copy(mediaId = "entry-next"), target = target.copy(entryId = "entry-next")),
                wrapper.copy(playback = ready.copy(durationMs = 200_000)),
                wrapper.copy(playback = ready.copy(playbackStatus = PlaybackStatus.Ended)),
                wrapper.copy(playback = ready.copy(seekEnabled = false))).forEach { changed ->
                val changedInput = DesktopTransportInput(changed, ready, true, true, DesktopSection.Player, 1)
                assertFalse(draft.matches(changedInput, true, false))
                assertFalse(lifetime == DesktopSeekLifetime(changedInput, false))
            }
            assertFalse(draft.matches(full, false, false))
            assertFalse(draft.matches(DesktopTransportInput(wrapper, ready, true, false, DesktopSection.Player, 1), true, false))
            assertFalse(draft.matches(DesktopTransportInput(wrapper, ready, true, true, DesktopSection.Player, 2), true, false))
            assertEquals(0L, draft.withFraction(-1f).positionMs())
            assertEquals(180_000L, draft.withFraction(2f).positionMs())
            assertEquals(null, draft.withFraction(Float.NaN).positionMs())
            val commands = mutableListOf<Triple<DesktopPlaybackTarget, DesktopTransportAction, Long?>>()
            var genericCalls = 0
            val callbacks = DesktopTransportCallbacks { identity, action, position -> commands += Triple(identity, action, position) }
            val legacy = DesktopPlaybackCallbacks({ genericCalls++ }, { genericCalls++ }, { genericCalls++ },
                { genericCalls++ }, { genericCalls++ })
            // Preparing, opening, and reading cached presentation issue no playback command.
            prepareDesktopPlayerView(input)
            assertTrue(commands.isEmpty())
            assertEquals(0, genericCalls)
            listOf(DesktopSection.Library to true, DesktopSection.Player to false).forEach { (section, allowLegacy) ->
                DesktopTransportAction.entries.forEach { action ->
                    val playing = action == DesktopTransportAction.PAUSE
                    val publication = wrapper.copy(playback = ready.copy(isPlaying = playing))
                    val captured = DesktopTransportInput(publication, PlaybackPresentationState(), true, true, section, 1)
                    assertTrue(dispatchDesktopTransport(captured, captured, action,
                        if (action == DesktopTransportAction.SEEK) draft.positionMs() else null, true, allowLegacy, callbacks, legacy))
                }
            }
            assertEquals(10, commands.size)
            assertEquals(commands.take(5), commands.drop(5))
            assertTrue(commands.all { it.first == target })
            assertEquals(0, genericCalls)
        }
        run {
            val track = DesktopTrack("a", "Z song", "Artist", 1_000, "a".repeat(64), 100, preference = "LIKED")
            val present = DesktopCopyInspection(track.id, track.sha256, track.byteSize, "PRESENT")
            assertEquals(DesktopCopyAvailability.PRESENT, desktopCopyAvailability(track, mapOf(track.id to present)))
            listOf(present.copy(trackId = "other"), present.copy(sha256 = "b".repeat(64)),
                present.copy(byteSize = 101), present.copy(status = "FUTURE"), present.copy(status = "present"),
                present.copy(status = "private://hidden/path")).forEach { observation ->
                assertEquals(DesktopCopyAvailability.UNKNOWN, desktopCopyAvailability(track, mapOf(track.id to observation)))
            }
            assertEquals(DesktopCopyAvailability.UNKNOWN, desktopCopyAvailability(track, emptyMap()))
            assertEquals(DesktopCopyAvailability.UNKNOWN, desktopCopyAvailability(track, mapOf("other-key" to present)))
            assertEquals(DesktopCopyAvailability.PRESENT,
                desktopCopyAvailability(track.copy(title = "Renamed", durationMs = 2_000), mapOf(track.id to present)))
            val earlier = track.copy(id = "b", title = "A song")
            val unavailable = track.copy(id = "unavailable", title = "B song")
            val removed = track.copy(id = "removed", collectionState = "REMOVED")
            val disliked = track.copy(id = "disliked", preference = "DISLIKED")
            val noQueryMatch = track.copy(id = "other-title", title = "Other title", artist = "Other artist")
            val observed = listOf(track, earlier, unavailable, removed, disliked, noQueryMatch)
            val results = observed.associate { it.id to DesktopCopyInspection(it.id, it.sha256, it.byteSize,
                if (it.id == unavailable.id) "UNAVAILABLE" else "PRESENT") }
            val unknown = List(10_001) { track.copy(id = "unknown-$it") }
            val catalog = unknown + observed
            val matches = prepareDesktopLibraryMatches(catalog, "SONG", DesktopTrackPreferenceFilter.LIKED,
                DesktopLibrarySort.Title, DesktopSortDirection.Ascending, DesktopTrackCollectionFilter.ACTIVE,
                DesktopCopyAvailabilityFilter.PRESENT, results).take(10_000)
            assertEquals(listOf("b", "a"), matches.map { it.id })
            assertEquals(listOf("unavailable"), prepareDesktopLibraryMatches(catalog, "song", DesktopTrackPreferenceFilter.LIKED,
                DesktopLibrarySort.Title, DesktopSortDirection.Ascending, DesktopTrackCollectionFilter.ACTIVE,
                DesktopCopyAvailabilityFilter.UNAVAILABLE, results).map { it.id })
            assertEquals(10_001, prepareDesktopLibraryMatches(catalog, "song", DesktopTrackPreferenceFilter.LIKED,
                DesktopLibrarySort.Title, DesktopSortDirection.Ascending, DesktopTrackCollectionFilter.ACTIVE,
                DesktopCopyAvailabilityFilter.UNKNOWN, results).size)
            assertEquals(listOf("removed"), prepareDesktopLibraryMatches(catalog, "song", DesktopTrackPreferenceFilter.LIKED,
                DesktopLibrarySort.Title, DesktopSortDirection.Ascending, DesktopTrackCollectionFilter.REMOVED,
                DesktopCopyAvailabilityFilter.PRESENT, results).map { it.id })
            val shown = prepareDesktopLibraryMatches(catalog, "song", DesktopTrackPreferenceFilter.LIKED,
                DesktopLibrarySort.Title, DesktopSortDirection.Descending, DesktopTrackCollectionFilter.ACTIVE,
                DesktopCopyAvailabilityFilter.ALL, results).take(10_000)
            assertEquals(shown.take(1_000).map { it.id }, prepareDesktopCopyInspectionIds(shown))
            assertEquals(1_000, prepareDesktopCopyInspectionIds(shown).size)
            assertEquals(1_000, DesktopCopyInspectionLimits.MAX_REQUEST_TRACKS)
            assertTrue(prepareDesktopCopyInspectionIds(emptyList()).isEmpty())
            assertEquals(listOf(track.id), prepareDesktopCopyInspectionIds(listOf(track, track)))
            var checkpoints = 0
            try {
                prepareDesktopCopyInspectionIds(shown) {
                    if (++checkpoints == 2) throw CancellationException("copy_cancelled")
                }
                fail("Inspection selection must cooperate with cancellation")
            } catch (expected: CancellationException) { assertEquals("copy_cancelled", expected.message) }
            checkpoints = 0
            try {
                prepareDesktopLibraryMatches(catalog, "", DesktopTrackPreferenceFilter.ALL, DesktopLibrarySort.Title,
                    DesktopSortDirection.Ascending, DesktopTrackCollectionFilter.ALL, DesktopCopyAvailabilityFilter.PRESENT, results) {
                    if (++checkpoints == 3) throw CancellationException("copy_filter_cancelled")
                }
                fail("Observation filtering must cooperate with cancellation")
            } catch (expected: CancellationException) { assertEquals("copy_filter_cancelled", expected.message) }
            // Unavailable evidence stays in playable selection; the actual source owner decides on play.
            assertEquals(listOf(track.id, unavailable.id), prepareDesktopLibraryCollectionSelection(listOf(track, unavailable)).playIds)
            assertEquals("ACTIVE", unavailable.collectionState)
            assertEquals("PRESENT", results[track.id]?.status)
            assertEquals("UNAVAILABLE", results[unavailable.id]?.status)
        }
        assertMetadataArtworkBounds()
        assertPlaylistSearchRoutes()
        assertHomeAndHistoryPreparation()
        assertEquals(DesktopPlaybackControls(true, true, true, true), DesktopPlaybackControls.from(readyState(), true))
        assertEquals(
            DesktopPlaybackControls(true, false, false, true),
            DesktopPlaybackControls.from(readyState().copy(previousMediaId = null, nextMediaId = null), true),
        )
        listOf(
            readyState().copy(isLive = true),
            readyState().copy(isSeekable = false),
            readyState().copy(seekEnabled = false),
            readyState().copy(durationMs = null),
            readyState().copy(durationMs = 0),
            readyState().copy(durationMs = -1),
        ).forEach { state ->
            val controls = DesktopPlaybackControls.from(state, true)
            assertTrue(controls.playPause)
            assertFalse(controls.seek)
        }
        assertLibraryOrdering()
        assertCollectionSelections()
        assertBrowsePageMath()
        assertPagedPlaylistPreparation()
        assertPagedCollectionSelections()
        assertTasteCapturedPolicy()
        val entries = List(10_001) { DesktopPlaylistEntry("entry-$it", "duplicate") }
        val playlist = DesktopPlaylist("playlist", "Name", entries)
        val input = DesktopPlaylistInput(DesktopLibrarySnapshot(playlists = listOf(playlist)), playlist.id)
        val view = prepareDesktopPlaylistView(input, 10_000)
        assertEquals(10_000, view.rows.size)
        assertFalse(view.rows.first().canMoveUp)
        assertTrue(view.rows.last().canMoveDown)
        assertEquals(10_000, view.rows.last().occurrence)
        assertEquals(9_999, view.entryPositions["entry-9999"])
        assertEquals(listOf("duplicate", "duplicate"), view.playIds.subList(9_998, view.playIds.size))
        val full = prepareDesktopPlaylistView(input, entries.size)
        assertFalse(full.rows.last().canMoveDown)
        assertEquals(entries, playlist.entries)
        var batches = 0
        try {
            prepareDesktopPlaylistView(input, 10_000) {
                if (++batches == 4) throw CancellationException("playlist_cancelled")
            }
            fail("Playlist preparation cancellation must propagate")
        } catch (expected: CancellationException) {
            assertEquals("playlist_cancelled", expected.message)
        }
        val empty = prepareDesktopPlaylistView(DesktopPlaylistInput(DesktopLibrarySnapshot(), null), 10_000)
        assertTrue(empty.rows.isEmpty())
        assertEquals(null, empty.playlist)
        val fallback = prepareDesktopPlaylistView(DesktopPlaylistInput(input.library, null), 10_000).playlist
        val other = DesktopPlaylist("other", "A", emptyList())
        val reorderedSnapshot = input.library.copy(playlists = listOf(other, playlist.copy(name = "Z")))
        val pinned = prepareDesktopPlaylistView(DesktopPlaylistInput(reorderedSnapshot, fallback?.id), 10_000)
        assertEquals(playlist.id, pinned.playlist?.id)
        val removed = prepareDesktopPlaylistView(DesktopPlaylistInput(reorderedSnapshot.copy(playlists = listOf(other)), playlist.id), 10_000)
        assertEquals(other.id, removed.playlist?.id)
        assertFalse(desktopPlaylistBulkAdmission(view.playlist, 0, true, true, false, true, false).hasCapacity)
        val fits = playlist.copy(entries = entries.take(3))
        val smallDisplay = prepareDesktopPlaylistView(DesktopPlaylistInput(input.library.copy(playlists = listOf(fits)), fits.id), 2)
        assertEquals(2, smallDisplay.rows.size)
        assertEquals(3, smallDisplay.playlist?.entries?.size)
        assertFalse(desktopPlaylistBulkAdmission(smallDisplay.playlist, 998, true, true, true, true, false).hasCapacity)
        val fullAdmission = desktopPlaylistBulkAdmission(fits, 997, true, true, true, true, false)
        assertTrue(fullAdmission.hasCapacity)
        assertTrue(fullAdmission.enqueue)
        assertTrue(fullAdmission.playNext)
        val missingCurrent = desktopPlaylistBulkAdmission(fits, 997, true, true, false, true, false)
        assertTrue(missingCurrent.enqueue)
        assertFalse(missingCurrent.playNext)
        assertTrue(desktopPlaylistBulkAdmission(fits, 0, true, true, false, true, false).playNext)
        listOf(
            desktopPlaylistBulkAdmission(fits, 998, true, true, true, true, false),
            desktopPlaylistBulkAdmission(fits, 1_001, true, true, true, true, false),
            desktopPlaylistBulkAdmission(fits, 0, false, true, false, true, false),
            desktopPlaylistBulkAdmission(fits, 0, true, false, false, true, false),
            desktopPlaylistBulkAdmission(fits, 0, true, true, false, false, false),
            desktopPlaylistBulkAdmission(fits, 0, true, true, false, true, true),
            desktopPlaylistBulkAdmission(fits.copy(entries = emptyList()), 0, true, true, false, true, false),
            desktopPlaylistBulkAdmission(null, 0, true, true, false, true, false),
        ).forEach { admission ->
            assertFalse(admission.enqueue)
            assertFalse(admission.playNext)
        }
        val populatedTarget = DesktopPlaylistDeleteTarget(playlist.id, playlist.name, entries.size)
        assertTrue(populatedTarget.matches(playlist.copy(entries = entries.reversed())))
        assertFalse(populatedTarget.matches(playlist.copy(entries = entries.take(10_000))))

        val descriptionPlaylist = fits.copy(description = "  Stored\nraw  ")
        val descriptionSnapshot = input.library.copy(playlists = listOf(descriptionPlaylist))
        val descriptionInput = DesktopPlaylistInput(descriptionSnapshot, descriptionPlaylist.id, 5)
        var descriptionNavigationCurrent = true
        val openedDescription = DesktopPlaylistDescriptionDraft(descriptionPlaylist.id, descriptionPlaylist.description,
            "  Edited\nexact  ", 5, { descriptionNavigationCurrent })
        var currentDescription: DesktopPlaylistDescriptionDraft? = openedDescription
        assertTrue(openedDescription.canAct(currentDescription, descriptionInput, false, true))
        assertFalse(openedDescription.canAct(currentDescription, descriptionInput, true, true))
        // Restart blocks writes while read-only dismissal remains available.
        assertTrue(openedDescription.canClose(currentDescription, true))
        assertFalse(openedDescription.canClose(currentDescription, false))
        assertFalse(openedDescription.canClose(null, true))
        assertFalse(openedDescription.canAct(currentDescription, descriptionInput, false, false))
        assertFalse(openedDescription.canAct(currentDescription, DesktopPlaylistInput(descriptionSnapshot, "other", 5), false, true))
        assertFalse(openedDescription.canAct(currentDescription, DesktopPlaylistInput(descriptionSnapshot, descriptionPlaylist.id, 7), false, true))
        descriptionNavigationCurrent = false
        assertFalse(openedDescription.canAct(currentDescription, descriptionInput, false, true))
        descriptionNavigationCurrent = true
        var descriptionNavigationRevision = 1L
        val originNavigationRevision = descriptionNavigationRevision
        val navigationDescription = DesktopPlaylistDescriptionDraft(descriptionPlaylist.id, descriptionPlaylist.description,
            "Navigation draft", 5, { descriptionNavigationRevision == originNavigationRevision })
        assertTrue(navigationDescription.canAct(navigationDescription, descriptionInput, false, true))
        descriptionNavigationRevision = 2
        assertFalse(navigationDescription.canAct(navigationDescription, descriptionInput, false, false))
        descriptionNavigationRevision = 3
        assertFalse(navigationDescription.canAct(navigationDescription, descriptionInput, false, true))
        val editedDescription = openedDescription.withText("  Newer\ntext  ")
        currentDescription = editedDescription
        assertFalse(openedDescription.canAct(currentDescription, descriptionInput, false, true))
        assertTrue(editedDescription.canAct(currentDescription, descriptionInput, false, true))
        assertFalse(openedDescription.canClose(currentDescription, true))
        assertTrue(editedDescription.canClose(currentDescription, true))
        val descriptionCommands = mutableListOf<Triple<String, String?, String?>>()
        var unrelatedCommands = 0
        val descriptionCallbacks = DesktopLibraryCallbacks(onRenamePlaylist = { _, _ -> unrelatedCommands++ },
            onPlayTracks = { _, _ -> unrelatedCommands++ }, onEnqueuePlaylist = { unrelatedCommands++ },
            onUpdatePlaylistDescription = { id, expected, value -> descriptionCommands += Triple(id, expected, value) })
        val capturedDescriptionSubmit = {
            if (editedDescription.canAct(currentDescription, descriptionInput, false, true) &&
                editedDescription.canSubmit(descriptionPlaylist)) {
                currentDescription = editedDescription.submitted(descriptionInput)
                descriptionCallbacks.onUpdatePlaylistDescription(editedDescription.playlistId,
                    editedDescription.baselineDescription, editedDescription.description)
            }
        }
        capturedDescriptionSubmit()
        capturedDescriptionSubmit()
        assertEquals(listOf(Triple(descriptionPlaylist.id, "  Stored\nraw  ", "  Newer\ntext  ")), descriptionCommands)
        assertEquals(0, unrelatedCommands)
        val submittedDescription = requireNotNull(currentDescription)
        assertFalse(submittedDescription.canSubmit(descriptionPlaylist))
        assertFalse(submittedDescription.confirmed(descriptionInput, descriptionPlaylist))
        val confirmedPlaylist = descriptionPlaylist.copy(description = editedDescription.description)
        val confirmedInput = DesktopPlaylistInput(descriptionSnapshot.copy(playlists = listOf(confirmedPlaylist)), descriptionPlaylist.id, 5)
        assertTrue(submittedDescription.confirmed(confirmedInput, confirmedPlaylist))
        assertFalse(submittedDescription.confirmed(confirmedInput, null))
        assertFalse(submittedDescription.confirmed(confirmedInput, confirmedPlaylist.copy(id = "other")))
        assertFalse(submittedDescription.confirmed(confirmedInput, descriptionPlaylist))
        // Failure/repeated identical error needs no observed transient state: review explicitly rearms the same text.
        val retriedDescription = submittedDescription.reviewed(descriptionInput, descriptionPlaylist) { true }
        currentDescription = retriedDescription
        assertEquals(submittedDescription.text, retriedDescription.text)
        assertTrue(retriedDescription.canSubmit(descriptionPlaylist))
        assertFalse(submittedDescription.canAct(currentDescription, descriptionInput, false, true))
        val externalPlaylist = descriptionPlaylist.copy(description = "External\nraw baseline")
        assertFalse(retriedDescription.matches(externalPlaylist))
        assertEquals("  Newer\ntext  ", retriedDescription.text)
        val externalInput = DesktopPlaylistInput(descriptionSnapshot.copy(playlists = listOf(externalPlaylist)), externalPlaylist.id, 5)
        val reviewedDescription = retriedDescription.reviewed(externalInput, externalPlaylist) { true }
        assertEquals(externalPlaylist.description, reviewedDescription.baselineDescription)
        assertEquals(retriedDescription.text, reviewedDescription.text)
        assertTrue(reviewedDescription.canSubmit(externalPlaylist))
        // A late result from an earlier submission cannot close or overwrite a newer edited draft.
        val newerDescription = retriedDescription.withText("  Newest\nunsaved text  ")
        assertFalse(newerDescription.confirmed(confirmedInput, confirmedPlaylist))
        assertFalse(newerDescription.matches(confirmedPlaylist))
        assertEquals("  Newest\nunsaved text  ", newerDescription.text)
        val newerReviewed = newerDescription.reviewed(confirmedInput, confirmedPlaylist) { true }
        assertEquals(newerDescription.text, newerReviewed.text)
        assertEquals(confirmedPlaylist.description, newerReviewed.baselineDescription)
        assertTrue(newerReviewed.canSubmit(confirmedPlaylist))
        currentDescription = null
        capturedDescriptionSubmit()
        currentDescription = DesktopPlaylistDescriptionDraft(editedDescription.playlistId, editedDescription.baselineDescription,
            editedDescription.text, 5)
        capturedDescriptionSubmit()
        assertEquals(1, descriptionCommands.size)
        assertFalse(editedDescription.canAct(currentDescription, descriptionInput, false, true))
        assertEquals(entries.take(3), descriptionPlaylist.entries)

        listOf("1" to 60_000L, "15" to 900_000L, "30" to 1_800_000L,
            "60" to 3_600_000L, "720" to 43_200_000L, "001" to 60_000L).forEach { (inputMinutes, expected) ->
            assertEquals(expected, desktopSleepTimerDurationMs(inputMinutes))
        }
        listOf("", "0", "000", "721", "999", "1000", "-1", "+1", "1.5", " 15", "15 ",
            "\u0661", "\uFF11", "1\n", "a", "9".repeat(1_000)).forEach {
            assertEquals(null, desktopSleepTimerDurationMs(it))
        }
        assertTrue(acceptsDesktopSleepTimerInput(""))
        assertFalse(acceptsDesktopSleepTimerInput("1234"))
        assertFalse(acceptsDesktopSleepTimerInput("\uFF11"))
        val timerTarget = DesktopSleepTimerTarget(DesktopSleepTimerState("queue", "entry", "session"))
        assertEquals(1_800_000L, DesktopSleepTimerDraft(timerTarget).durationMs())
        assertEquals(null, DesktopSleepTimerDraft(timerTarget, "30", inputRejected = true).durationMs())
        listOf(-1L to 0L, 0L to 0L, 1L to 1L, 60_000L to 1L,
            60_001L to 2L, 43_200_000L to 720L, Long.MAX_VALUE to 153_722_867_280_913L).forEach { (remaining, minutes) ->
            assertEquals(minutes, desktopSleepTimerRemainingMinutes(remaining))
        }

        listOf(false, true).forEach { systemDark ->
            assertEquals(systemDark, desktopUsesDarkTheme(DesktopThemeMode.System, systemDark))
            assertTrue(desktopUsesDarkTheme(DesktopThemeMode.Dark, systemDark))
            assertFalse(desktopUsesDarkTheme(DesktopThemeMode.Light, systemDark))
        }
        DesktopThemeMode.entries.forEach { assertEquals(it, desktopPreferencesTheme(it.settingsValue)) }
        assertEquals(setOf("CORAL", "VIOLET", "GREEN", "BLUE"), DesktopAccentPalette.entries.map { it.settingsValue }.toSet())
        DesktopAccentPalette.entries.forEach { accent ->
            assertEquals(accent, desktopPreferencesAccent(accent.settingsValue))
            val dark = desktopColorScheme(true, accent)
            val light = desktopColorScheme(false, accent)
            assertEquals(accent.darkPrimary, dark.primary)
            assertEquals(accent.lightPrimary, light.primary)
            assertEquals(desktopColorScheme(true, DesktopAccentPalette.Coral).onPrimary, dark.onPrimary)
            assertEquals(desktopColorScheme(false, DesktopAccentPalette.Coral).onPrimaryContainer, light.onPrimaryContainer)
            assertFalse(desktopSettingsHaveUnsupportedValues(DesktopPreferences(accentPalette = accent.settingsValue)))
        }
        assertEquals(DesktopLanguage.Russian, desktopPreferencesLanguage("RU"))
        assertEquals(DesktopLanguage.English, desktopPreferencesLanguage("EN"))
        listOf("", "ru", "CUSTOM", "\uD83C\uDFB5", "a".repeat(65_537), "\u0000\u202Eprivate").forEach { unknown ->
            val source = DesktopPreferences(unknown, unknown, unknown)
            assertTrue(desktopSettingsHaveUnsupportedValues(source))
            assertEquals(DesktopLanguage.English, desktopPreferencesLanguage(source.appLanguage))
            assertEquals(DesktopLanguage.Russian, desktopPreferencesLanguage(source.appLanguage, DesktopLanguage.Russian))
            assertEquals(DesktopThemeMode.Dark, desktopPreferencesTheme(source.appearanceMode))
            assertEquals(DesktopAccentPalette.Coral, desktopPreferencesAccent(source.accentPalette))
            assertEquals(unknown, source.appLanguage)
            assertEquals(unknown, source.appearanceMode)
            assertEquals(unknown, source.accentPalette)
            assertEquals(DesktopText.SettingsOtherValue.inLanguage(DesktopLanguage.English),
                desktopSettingsValueLabel(DesktopText.Language, unknown, DesktopLanguage.English))
        }
    }

    @Test
    public fun bothLanguagesCoverEveryVisibleCopyEntry(): Unit {
        assertSavedImportsCopy()
        assertSearchCopy()
        assertLifecycleCopy()
        assertUpcomingCopy()
        DesktopLanguage.entries.forEach { language ->
            listOf(DesktopText.MultiSelectionCount, DesktopText.MultiSelectionOrder, DesktopText.SelectActivePage,
                DesktopText.DeselectPage, DesktopText.ClearSelection, DesktopText.SelectTrack, DesktopText.DeselectTrack,
                DesktopText.MultiSelectionInvalidCount, DesktopText.MultiSelectionInvalid, DesktopText.MultiSelectionFilterCleared,
                DesktopText.MultiSelectionLimitRejected, DesktopText.MultiSelectionSubmitted, DesktopText.PlaySelected,
                DesktopText.EnqueueSelected, DesktopText.PlaySelectedNext, DesktopText.AddSelectedToPlaylist,
                DesktopText.MultiSelectionUnavailableTrack, DesktopText.PlaylistBatchLimit, DesktopText.PlaylistAppendLimit,
                DesktopText.PlaylistOrderUnsupported, DesktopText.ReferencedTrackMissing, DesktopText.CommandIdentityInvalid,
                DesktopText.ReferencedPlaylistMissing).forEach {
                assertTrue(it.inLanguage(language).isNotBlank())
            }
            assertTrue(DesktopText.MultiSelectionCount.inLanguage(language).replace("{count}", "999")
                .replace("{limit}", "1000").contains("999 / 1000"))
            assertFalse(DesktopText.MultiSelectionLimitRejected.inLanguage(language).replace("{limit}", "1000").contains("{"))
            assertFalse(DesktopText.MultiSelectionInvalidCount.inLanguage(language).replace("{count}", "3").contains("{"))
            listOf("QUEUE_LIMIT", "desktop_track_not_in_collection", "desktop_playlist_missing", "LIBRARY_COMMAND_BUSY",
                "PLAYBACK_COMMAND_BUSY", "private://unexpected").forEach {
                assertTrue(desktopErrorMessage(it, language).isNotBlank())
                assertFalse(desktopErrorMessage(it, language).contains("private://"))
            }
            mapOf("desktop_playlist_limit" to DesktopText.PlaylistAppendLimit,
                "desktop_playlist_missing" to DesktopText.ReferencedPlaylistMissing,
                "desktop_playlist_order_invalid" to DesktopText.PlaylistOrderUnsupported,
                "desktop_track_missing" to DesktopText.ReferencedTrackMissing,
                "desktop_identity_invalid" to DesktopText.CommandIdentityInvalid).forEach { (code, text) ->
                assertEquals(text.inLanguage(language), desktopErrorMessage(code, language))
            }
            assertEquals(DesktopText.PlaylistBatchLimit.inLanguage(language).replace("{limit}", "1000"),
                desktopErrorMessage("desktop_playlist_batch_limit", language))
            assertEquals(DesktopText.OperationFailed.inLanguage(language), desktopErrorMessage("private://unexpected", language))
        }
        assertTrue(DesktopText.MultiSelectionSubmitted.english.contains("unconfirmed"))
        assertTrue(DesktopText.MultiSelectionInvalid.english.contains("All group commands are blocked"))
        assertTrue(DesktopText.MultiSelectionFilterCleared.english.contains("query or filter changed"))
        assertEquals("Play selected", DesktopText.PlaySelected.english)
        assertEquals("\u0421\u043b\u0443\u0448\u0430\u0442\u044c \u0432\u044b\u0431\u0440\u0430\u043d\u043d\u044b\u0435", DesktopText.PlaySelected.russian)
        DesktopLanguage.entries.forEach { language ->
            listOf(DesktopText.PlayPage, DesktopText.PagePlaybackBoundary, DesktopText.WholeResultQueueLimit,
                DesktopText.PlaylistPageUnavailable, DesktopText.BrowseRange, DesktopText.BrowsePage,
                DesktopText.BrowseFirst, DesktopText.BrowsePrevious, DesktopText.BrowseNext,
                DesktopText.BrowseLast, DesktopText.PlaylistEntries).forEach { text ->
                assertTrue(text.inLanguage(language).isNotBlank())
            }
            val limit = DesktopText.WholeResultQueueLimit.inLanguage(language).replace("{limit}", "1000")
            assertTrue(limit.contains("1000"))
            assertFalse(limit.contains("{limit}"))
            val page = desktopBrowsePage(10_005, Int.MAX_VALUE)
            val range = DesktopText.BrowseRange.inLanguage(language).replace("{start}", (page.start + 1).toString())
                .replace("{end}", page.end.toString()).replace("{total}", page.total.toString())
            assertTrue(range.contains("10001\u201310005"))
            assertFalse(range.contains("{"))
            val position = DesktopText.BrowsePage.inLanguage(language).replace("{page}", (page.index + 1).toString())
                .replace("{pages}", page.pages.toString())
            assertTrue(position.contains("101"))
            assertFalse(position.contains("{"))
            val emptyRange = DesktopText.BrowseRange.inLanguage(language).replace("{start}", "0")
                .replace("{end}", "0").replace("{total}", "0")
            assertTrue(emptyRange.contains("0\u20130"))
        }
        assertEquals("Play page", DesktopText.PlayPage.inLanguage(DesktopLanguage.English))
        assertEquals("\u0421\u043b\u0443\u0448\u0430\u0442\u044c \u0441\u0442\u0440\u0430\u043d\u0438\u0446\u0443",
            DesktopText.PlayPage.inLanguage(DesktopLanguage.Russian))
        assertTrue(DesktopText.PagePlaybackBoundary.inLanguage(DesktopLanguage.English).contains("end of this page"))
        assertTrue(DesktopText.PlaylistPageUnavailable.inLanguage(DesktopLanguage.English).contains("every entry"))
        DesktopLanguage.entries.forEach { language ->
            DesktopImportText.entries.forEach { assertTrue(it.label(language).isNotBlank()) }
            DesktopImportPhase.entries.forEach { phase ->
                assertEquals(phase == DesktopImportPhase.IDLE, desktopImportPhaseText(phase) == null)
            }
            val result = DesktopImportResult(3, 2, 1, cancelled = true)
            assertTrue(desktopImportWasCancelled(result, null))
            assertTrue(desktopImportWasCancelled(result.copy(cancelled = false, errorCode = "IMPORT_CANCELLED"), null))
            assertTrue(desktopImportWasCancelled(result.copy(cancelled = false), "IMPORT_CANCELLED"))
            assertFalse(desktopImportWasCancelled(result.copy(cancelled = false), null))
            val summary = desktopImportResultSummary(result, null, language)
            assertTrue(summary.contains(DesktopText.ImportCancelled.inLanguage(language)))
            assertTrue(summary.contains(DesktopImportText.LastReported.label(language)))
            assertTrue(summary.contains("${DesktopImportText.SavedBeforeStop.label(language)}: 3"))
            assertTrue(summary.contains("${DesktopText.Duplicates.inLanguage(language)}: 2"))
            assertTrue(summary.contains("${DesktopText.Failed.inLanguage(language)}: 1"))
            assertFalse(summary.contains("${DesktopText.Imported.inLanguage(language)}:"))
            val settled = DesktopImportProgress("preview", 6, 5, 3, 1, 1, "Song")
            assertTrue(desktopImportProgressSummary(settled, language).contains("5 / 6"))
            assertTrue(desktopImportProgressSummary(settled.copy(totalFiles = -1, completedFiles = Int.MAX_VALUE), language).contains("0 / 0"))
            assertEquals(desktopImportResultSummary(DesktopImportResult(2_048, 2_048, 16_384), null, language),
                desktopImportResultSummary(DesktopImportResult(Int.MAX_VALUE, Int.MAX_VALUE, Int.MAX_VALUE), null, language))
            assertEquals(desktopImportResultSummary(DesktopImportResult(0, 0, 0), null, language),
                desktopImportResultSummary(DesktopImportResult(-1, -2, Int.MIN_VALUE), null, language))
            assertEquals("0 ${DesktopImportText.Bytes.label(language)}", desktopImportBytes(-1, language))
            listOf(null, "", "/private/path/song", "C:\\private\\song", "private://source", "Song\u0000", "Song\u202Etxt",
                "\uD800", "x".repeat(513)).forEach { unsafe ->
                assertEquals(DesktopImportText.Unnamed.label(language), desktopImportSafeTitle(unsafe, language))
            }
            assertEquals("Song \uD83C\uDFB5", desktopImportSafeTitle("Song \uD83C\uDFB5", language))
            assertEquals(DesktopImportText.Changed.label(language), desktopErrorMessage("import_preview_expired", language))
            assertEquals(DesktopImportText.SourceUnavailable.label(language), desktopErrorMessage("import_access_denied", language))
            assertEquals(DesktopImportText.Failed, desktopImportErrorText("private://raw-exception"))
            assertFalse(desktopErrorMessage("private://raw-exception", language).contains("private://"))
        }
        assertTrue(DesktopImportText.Frozen.english.contains("whole frozen list"))
        assertTrue(DesktopImportText.Frozen.russian.contains("\u0432\u0441\u0435\u043c\u0443"))
        assertTrue(DesktopImportText.Retained.english.contains("remain in the library"))
        assertEquals("Last reported counts", DesktopImportText.LastReported.english)
        assertEquals("\u041f\u043e\u0441\u043b\u0435\u0434\u043d\u0438\u0435 \u043f\u043e\u043b\u0443\u0447\u0435\u043d\u043d\u044b\u0435 \u0440\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442\u044b",
            DesktopImportText.LastReported.russian)
        assertTrue(DesktopImportText.Limit.english.contains("2048"))
        assertEquals("Плеер", DesktopSection.Player.label(DesktopLanguage.Russian))
        assertEquals("Player", DesktopSection.Player.label(DesktopLanguage.English))
        assertEquals(DesktopSection.Home, DesktopSection.entries.first())
        DesktopLanguage.entries.forEach { language ->
            listOf(DesktopText.Player, DesktopText.PlayerEmpty, DesktopText.PlayerReadOnly, DesktopText.PlayerPrevious,
                DesktopText.PlayerCurrent, DesktopText.PlayerNext, DesktopText.PlayerNoNeighbor, DesktopText.PlayerEntryMissing,
                DesktopText.OpenPlayer, DesktopText.OpenQueue).forEach { assertTrue(it.inLanguage(language).isNotBlank()) }
        }
        DesktopLanguage.entries.forEach { language ->
            DesktopCopyText.entries.forEach { assertTrue(it.label(language).isNotBlank()) }
            DesktopCopyAvailabilityFilter.entries.forEach { assertTrue(it.label(language).isNotBlank()) }
            val track = DesktopTrack("copy", "Song", "Artist", null, "a".repeat(64), 100)
            assertEquals(DesktopCopyText.Unknown.label(language), desktopCopyStatus(track, emptyMap(), language))
            listOf("PRESENT" to DesktopCopyText.Present, "UNAVAILABLE" to DesktopCopyText.Unavailable,
                "private://raw-path" to DesktopCopyText.Unknown).forEach { (status, copy) ->
                assertEquals(copy.label(language), desktopCopyStatus(track,
                    mapOf(track.id to DesktopCopyInspection(track.id, track.sha256, track.byteSize, status)), language))
            }
            val progress = DesktopCopyInspectionState(requested = 3, completed = 2)
            assertTrue(desktopCopyProgress(progress, language).contains("2"))
            assertTrue(desktopCopyProgress(progress, language).contains("3"))
            assertEquals(desktopCopyProgress(progress.copy(requested = 1_000, completed = 1_000), language),
                desktopCopyProgress(progress.copy(requested = Int.MAX_VALUE, completed = Int.MAX_VALUE), language))
            assertEquals(desktopCopyProgress(progress.copy(requested = 0, completed = 0), language),
                desktopCopyProgress(progress.copy(requested = -1, completed = -10), language))
            assertTrue(DesktopCopyText.InspectShown.label(language).replace("{limit}", "1000").contains("1000"))
            assertFalse(DesktopCopyText.Failed.label(language).contains("private://raw-path"))
        }
        assertTrue(DesktopCopyText.Hint.english.contains("attribute/size"))
        assertTrue(DesktopCopyText.Hint.english.contains("neither hash integrity nor decoding"))
        assertTrue(DesktopCopyText.Hint.russian.contains("атрибутов и размера"))
        assertTrue(DesktopCopyText.Hint.russian.contains("не проверяет хеш или декодирование"))
        assertTrue(DesktopCopyText.Reimport.english.contains("same bytes"))
        assertTrue(DesktopCopyText.Reimport.russian.contains("теми же байтами"))
        assertMetadataCopy()
        assertEquals(DesktopSection.Home, DesktopSection.entries.first())
        assertEquals("Главная", DesktopSection.Home.label(DesktopLanguage.Russian))
        assertEquals("Home", DesktopSection.Home.label(DesktopLanguage.English))
        DesktopLanguage.entries.forEach { language ->
            assertTrue(DesktopText.LocalSearchHint.inLanguage(language).contains("256"))
            assertTrue(DesktopText.HistoryWindow.inLanguage(language).contains("100"))
            assertTrue(DesktopText.HomeOffline.inLanguage(language).isNotBlank())
            assertTrue(DesktopText.HomeServerUnavailable.inLanguage(language).isNotBlank())
            listOf(DesktopText.HomeWelcome, DesktopText.ContinueListening, DesktopText.HomeNoContinuation,
                DesktopText.SearchPlaylists, DesktopText.PlaylistSearchBound, DesktopText.SearchMatchCount,
                DesktopText.HistoryRefresh, DesktopText.HistoryLoadMore, DesktopText.HistoryRetry,
                DesktopText.HistoryLoading, DesktopText.HistoryFailure, DesktopText.HistoryNotLoaded,
                DesktopText.HistoryNewerHidden).forEach { assertTrue(it.inLanguage(language).isNotBlank()) }
        }
        assertTasteCopyAndErrors()
        assertEquals("Редактировать описание", DesktopText.EditPlaylistDescription.inLanguage(DesktopLanguage.Russian))
        assertEquals("Apply", DesktopText.ApplyPlaylistDescription.inLanguage(DesktopLanguage.English))
        DesktopLanguage.entries.forEach { language ->
            assertTrue(DesktopText.PlaylistDescriptionHint.inLanguage(language).contains("500"))
            listOf("desktop_playlist_description_invalid" to DesktopText.PlaylistDescriptionHint,
                "desktop_playlist_description_conflict" to DesktopText.PlaylistDescriptionConflict,
                "desktop_playlist_missing" to DesktopText.PlaylistMissing).forEach { (code, copy) ->
                assertEquals(copy, desktopPlaylistDescriptionErrorText(code, false))
                val globalCopy = if (code == "desktop_playlist_missing") DesktopText.ReferencedPlaylistMissing else copy
                assertEquals(globalCopy.inLanguage(language), desktopErrorMessage(code, language))
                assertEquals(DesktopText.LibraryReopenRequired, desktopPlaylistDescriptionErrorText(code, true))
            }
            listOf("desktop_snapshot_unavailable", "import_library_reopen_required").forEach { code ->
                assertEquals(DesktopText.LibraryReopenRequired, desktopPlaylistDescriptionErrorText(code, false))
            }
            listOf("private://raw-text", "LIBRARY_COMMAND_BUSY", "LIBRARY_OPERATION_FAILED", "AUDIO_ERROR").forEach { code ->
                assertEquals(DesktopText.PlaylistDescriptionUnconfirmed, desktopPlaylistDescriptionErrorText(code, false))
                assertFalse(requireNotNull(desktopPlaylistDescriptionErrorText(code, false)).inLanguage(language).contains(code))
            }
            assertEquals(null, desktopPlaylistDescriptionErrorText(null, false))
            assertEquals(DesktopText.LibraryReopenRequired, desktopPlaylistDescriptionErrorText(null, true))
        }
        DesktopLanguage.entries.forEach { language ->
            DesktopText.entries.forEach { text -> assertTrue(text.inLanguage(language).isNotBlank()) }
            DesktopSection.entries.forEach { section -> assertTrue(section.label(language).isNotBlank()) }
            DesktopLibrarySort.entries.forEach { sort -> assertTrue(sort.label(language).isNotBlank()) }
            DesktopSortDirection.entries.forEach { direction -> assertTrue(direction.label(language).isNotBlank()) }
            DesktopTrackPreference.entries.forEach { preference -> assertTrue(preference.label(language).isNotBlank()) }
            DesktopTrackPreferenceFilter.entries.forEach { filter -> assertTrue(filter.label(language).isNotBlank()) }
            DesktopTrackCollectionFilter.entries.forEach { filter -> assertTrue(filter.label(language).isNotBlank()) }
        }
        assertEquals("Библиотека", DesktopSection.Library.label(DesktopLanguage.Russian))
        assertEquals("Settings", DesktopSection.Settings.label(DesktopLanguage.English))
        assertEquals("Переименовать плейлист", DesktopText.RenamePlaylist.inLanguage(DesktopLanguage.Russian))
        assertEquals("Apply", DesktopText.ApplyPlaylistName.inLanguage(DesktopLanguage.English))
        assertEquals("Cancel", DesktopText.CancelPlaylistRename.inLanguage(DesktopLanguage.English))
        assertEquals("Редактировать сведения", DesktopText.EditTrackMetadata.inLanguage(DesktopLanguage.Russian))
        assertEquals("Apply", DesktopText.ApplyTrackMetadata.inLanguage(DesktopLanguage.English))
        assertEquals("Cancel", DesktopText.CancelTrackMetadata.inLanguage(DesktopLanguage.English))
        assertEquals("Enqueue playlist", DesktopText.EnqueuePlaylist.inLanguage(DesktopLanguage.English))
        assertEquals("Весь плейлист следующим", DesktopText.PlayPlaylistNext.inLanguage(DesktopLanguage.Russian))
        assertEquals("Таймер сна", DesktopText.SleepTimer.inLanguage(DesktopLanguage.Russian))
        assertEquals("Apply", DesktopText.ApplySleepTimer.inLanguage(DesktopLanguage.English))
        assertEquals("Cancel", DesktopText.CancelSleepTimerDraft.inLanguage(DesktopLanguage.English))
        assertEquals("Cancel timer", DesktopText.CancelSleepTimer.inLanguage(DesktopLanguage.English))
        assertEquals("Нравится", DesktopTrackPreference.LIKED.label(DesktopLanguage.Russian))
        assertEquals("Disliked", DesktopTrackPreference.DISLIKED.label(DesktopLanguage.English))
        assertEquals("Без предпочтения", DesktopTrackPreference.NEUTRAL.label(DesktopLanguage.Russian))
        assertEquals("All", DesktopTrackPreferenceFilter.ALL.label(DesktopLanguage.English))
        assertEquals("Сбросить предпочтение", DesktopText.ClearTrackPreference.inLanguage(DesktopLanguage.Russian))
        assertEquals("Clear preference", DesktopText.ClearTrackPreference.inLanguage(DesktopLanguage.English))
        assertTrue(DesktopText.TrackPreferenceHint.english.contains("does not automatically skip"))
        assertTrue(DesktopText.TrackPreferenceHint.russian.contains("не пропускает трек автоматически"))
        assertTrue(DesktopText.TrackPreferenceHint.english.contains("not sent to the server"))
        assertTrue(DesktopText.TrackPreferenceHint.russian.contains("не передаётся серверу"))
        assertTrue(DesktopText.TrackPreferenceHint.english.contains("infer musical taste"))
        assertTrue(DesktopText.TrackPreferenceHint.russian.contains("вывода о музыкальном вкусе"))
        DesktopLanguage.entries.forEach { language ->
            val description = DesktopText.PlaylistEntryPosition.inLanguage(language)
            assertTrue(description.contains("{position}"))
            assertTrue(description.contains("{occurrence}"))
            assertTrue(DesktopText.DeletePlaylistCount.inLanguage(language).contains("{count}"))
            val capacity = DesktopText.PlaylistQueueCapacity.inLanguage(language)
            assertTrue(capacity.contains("{count}"))
            assertTrue(capacity.contains("{remaining}"))
            assertTrue(DesktopText.TrackMetadataHint.inLanguage(language).contains("4096"))
            DesktopTrackPreference.entries.forEach { preference ->
                assertEquals(preference.label(language), desktopTrackPreferenceStatus(preference.storedValue, language))
            }
            listOf("FUTURE", "liked", "", "\u0000\u202Eprivate", "x".repeat(4097)).forEach { unknown ->
                assertEquals(DesktopText.TrackPreferenceUnknown.inLanguage(language), desktopTrackPreferenceStatus(unknown, language))
            }
            assertTrue(DesktopText.DeletePlaylistRetained.inLanguage(language).isNotBlank())
            assertEquals(DesktopText.CollectionActive.inLanguage(language), desktopTrackCollectionStatus("ACTIVE", language))
            assertEquals(DesktopText.CollectionRemoved.inLanguage(language), desktopTrackCollectionStatus("REMOVED", language))
            listOf("FUTURE", "active", " ACTIVE", "REMOVED ", "", "\u0000\u202Eprivate", "x".repeat(4097)).forEach { unknown ->
                assertEquals(DesktopText.CollectionUnknown.inLanguage(language), desktopTrackCollectionStatus(unknown, language))
            }
            assertEquals(DesktopText.TrackNotInCollection.inLanguage(language), desktopErrorMessage("desktop_track_not_in_collection", language))
            assertEquals(DesktopText.CollectionStateInvalid.inLanguage(language), desktopErrorMessage("desktop_collection_state_invalid", language))
            assertEquals(DesktopText.OperationFailed.inLanguage(language), desktopErrorMessage("private://unknown", language))
            assertTrue(DesktopText.RemoveTrackRetained.inLanguage(language).isNotBlank())
            assertTrue(DesktopText.CollectionUnknownHint.inLanguage(language).contains(DesktopText.RestoreTrackToCollection.inLanguage(language)))
            assertTrue(DesktopText.SleepTimerMinutesHint.inLanguage(language).contains("720"))
            val timer = DesktopSleepTimerState("queue", "entry", "session", "plan", 60_001)
            assertEquals(DesktopText.SleepTimerRemaining.inLanguage(language).replace("{minutes}", "2"),
                desktopSleepTimerStatus(timer, language))
            assertEquals(DesktopText.SleepTimerAfterCurrent.inLanguage(language),
                desktopSleepTimerStatus(timer.copy(remainingMs = null, stopAfterEntryId = "entry"), language))
            assertEquals(DesktopText.SleepTimerOff.inLanguage(language),
                desktopSleepTimerStatus(timer.copy(planId = null, remainingMs = null), language))
            assertTrue(DesktopText.SleepTimerLifetime.inLanguage(language).contains("AutPlay"))
            DesktopThemeMode.entries.forEach { assertTrue(it.settingsLabel(language).isNotBlank()) }
            DesktopAccentPalette.entries.forEach { assertTrue(it.settingsLabel(language).isNotBlank()) }
            val retained = DesktopPreferences(downloadOnMeteredNetwork = true, wavePrefetchMode = "NEXT_3", smoothTrackTransitions = true)
            val retainedCopy = desktopRetainedSettings(retained, language)
            assertEquals(listOf(DesktopText.MeteredDownloads, DesktopText.WavePrefetch), retainedCopy.map { it.first })
            assertEquals(DesktopText.SettingsEnabled.inLanguage(language), retainedCopy.first().second)
            assertEquals(DesktopText.PrefetchNextThree.inLanguage(language), retainedCopy[1].second)
            assertEquals(DesktopText.SettingsEnabled.inLanguage(language), desktopSettingsBooleanLabel(true, language))
            assertEquals(DesktopText.SettingsDisabled.inLanguage(language), desktopSettingsBooleanLabel(false, language))
            assertTrue(DesktopText.SmoothTransitions.inLanguage(language).isNotBlank())
            assertTrue(DesktopText.SmoothTransitionsHint.inLanguage(language).isNotBlank())
            assertEquals(DesktopText.SettingsDisabled.inLanguage(language), desktopRetainedSettings(DesktopPreferences(), language).first().second)
            assertEquals(DesktopText.SettingsOtherValue.inLanguage(language),
                desktopRetainedSettings(retained.copy(wavePrefetchMode = "FUTURE"), language)[1].second)
            assertTrue(DesktopText.RetainedSettings.inLanguage(language).contains("Mac"))
            assertTrue(DesktopText.SettingsPreviewHint.inLanguage(language).contains(DesktopText.ApplySettingsImport.inLanguage(language)))
            assertTrue(DesktopText.SettingsImportTooLarge.inLanguage(language).contains("64"))
            assertFalse(DesktopText.SettingsTransferFailed.inLanguage(language).contains("Original settings are preserved"))
            assertFalse(DesktopText.SettingsTransferFailed.inLanguage(language).contains("Исходные настройки сохранены"))
            assertEquals(DesktopText.SettingsImported.inLanguage(language), desktopSettingsMessageText("desktop_settings_imported").inLanguage(language))
            listOf("desktop_settings_schema_unsupported", "desktop_settings_too_large", "desktop_settings_appearance_invalid",
                "desktop_settings_export_failed", "desktop_settings_cancelled", "private://unknown").forEach {
                assertTrue(desktopSettingsErrorText(it).inLanguage(language).isNotBlank())
                assertFalse(desktopSettingsErrorText(it).inLanguage(language).contains(it))
            }
        }
    }

    private class SearchFixture : DesktopTextSearchPort {
        var calls: Int = 0
        var answer: (DesktopLibrarySnapshot, String) -> DesktopTextSearchMatches = { _, query -> DesktopTextSearchMatches(query, true) }
        override suspend fun search(snapshot: DesktopLibrarySnapshot, query: String): DesktopTextSearchMatches {
            calls++
            return answer(snapshot, query)
        }
        override fun retire(): Unit = Unit
        override suspend fun close(): Unit = Unit
        override fun equals(other: Any?): Boolean = other is SearchFixture
        override fun hashCode(): Int = 1
    }

    private fun assertSearchTargets(): Unit {
        val snapshot = DesktopLibrarySnapshot()
        val port = SearchFixture()
        var currentPort: DesktopTextSearchPort? = port
        val lifetime = DesktopBrowseLifetime()
        var navigation = 1L
        val guard = desktopSearchOwnerGuard(port, { currentPort }) { lifetime.isActive && navigation == 1L }
        fun input(query: String = "a", revision: Long = 1, library: DesktopLibrarySnapshot = snapshot,
            owner: DesktopTextSearchPort? = port, page: Int = 0, section: DesktopSection = DesktopSection.Library,
            navigationRevision: Long = 1): DesktopLibraryInput = DesktopLibraryInput(
            library, query, DesktopLibrarySort.Title, DesktopSortDirection.Ascending,
            DesktopPlaylistInput(library, null), viewRevision = revision, textSearch = owner, page = page,
            section = section, navigationRevision = navigationRevision)
        val captured = input()
        assertTrue(guard())
        assertFalse(captured == input(query = "b", revision = 2))
        assertFalse(captured == input(revision = 3)) // Query A/B/A cannot revive the old preparation.
        assertFalse(captured == input(library = snapshot.copy()))
        assertFalse(captured == input(page = 1))
        assertFalse(captured == input(section = DesktopSection.Playlists))
        assertFalse(captured == input(navigationRevision = 3))
        val equalPort = SearchFixture()
        assertEquals(port, equalPort)
        assertEquals(DesktopSearchOwnerKey(port), DesktopSearchOwnerKey(port))
        assertFalse(DesktopSearchOwnerKey(port) == DesktopSearchOwnerKey(equalPort))
        currentPort = equalPort
        assertFalse(guard())
        assertFalse(captured == input(owner = equalPort))
        currentPort = port
        navigation = 2
        assertFalse(guard())
        navigation = 1
        lifetime.retire()
        assertFalse(guard())
        assertTrue(desktopSearchOwnerGuard(null, { null }) { true }())
        DesktopSection.entries.forEach { section ->
            assertEquals(if (section == DesktopSection.Library) "held" else "",
                desktopActiveSearchQuery(section, playlists = false, query = "held"))
            assertEquals(if (section == DesktopSection.Playlists) "held" else "",
                desktopActiveSearchQuery(section, playlists = true, query = "held"))
        }
    }

    private fun assertSearchPreparation(): Unit = runBlocking {
        val tracks = List(1_205) { index -> DesktopTrack("fts-${index.toString().padStart(4, '0')}",
            "unrelated ${index.toString().padStart(4, '0')}", "display artist", null, "a".repeat(64), 1) }
        val playlists = List(1_205) { index -> DesktopPlaylist("fts-list-$index", "List $index", emptyList(), "description") }
        val snapshot = DesktopLibrarySnapshot(tracks, playlists)
        val port = SearchFixture().apply {
            answer = { supplied, query ->
                assertSame(snapshot, supplied)
                DesktopTextSearchMatches(query, true, tracks.map { it.id }.reversed(), playlists.map { it.id }.reversed())
            }
        }
        val result = loadDesktopSearchMatches(port, snapshot, "unrelated")
        assertEquals(null, result.errorCode)
        assertEquals(1_205, result.trackIds.size)
        assertEquals(1_205, result.playlistIds.size)
        val candidates = prepareDesktopLibraryMatches(desktopSearchTracks(tracks, result), "",
            DesktopTrackPreferenceFilter.ALL, DesktopLibrarySort.Title, DesktopSortDirection.Ascending)
        assertEquals(tracks.map { it.id }, candidates.map { it.id }) // Explicit title sort wins over FTS rank.
        val visited = mutableListOf<String>()
        repeat(13) { page -> visited += desktopBrowseSlice(candidates, desktopBrowsePage(candidates.size, page)).map { it.id } }
        assertEquals(tracks.map { it.id }, visited)
        assertEquals(5, desktopBrowseSlice(candidates, desktopBrowsePage(candidates.size, 12)).size)
        assertTrue(prepareDesktopLibraryResultSelection(candidates).overflow)
        assertTrue(prepareDesktopLibraryResultSelection(candidates).playIds.isEmpty())
        assertEquals(1_000, prepareDesktopLibraryResultSelection(candidates.take(1_000)).playIds.size)
        val selected = playlists.last()
        val playlistInput = DesktopPlaylistInput(snapshot, selected.id, query = "does-not-occur", choicePage = 12)
        val playlistView = prepareDesktopPagedPlaylistView(playlistInput, desktopSearchPlaylistIds(playlists, result))
        assertEquals(1_205, playlistView.matchCount)
        assertEquals(5, playlistView.choices.size)
        assertSame(selected, playlistView.playlist)

        // The port's IDs are authoritative even if every excluded title contains the query.
        port.answer = { _, query -> DesktopTextSearchMatches(query, true, listOf(tracks.last().id), listOf(selected.id)) }
        val one = loadDesktopSearchMatches(port, snapshot, "unrelated")
        assertEquals(listOf(tracks.last()), desktopSearchTracks(tracks, one))
        assertEquals(1, prepareDesktopPagedPlaylistView(playlistInput, desktopSearchIds(one.playlistIds)).matchCount)
        val beforeBlank = port.calls
        assertSame(tracks, desktopSearchTracks(tracks, loadDesktopSearchMatches(port, snapshot, "  ")))
        assertTrue(desktopSearchTracks(tracks, loadDesktopSearchMatches(port, snapshot, "-*()\" ")).isEmpty())
        assertEquals(beforeBlank, port.calls)
        assertSame(tracks, desktopSearchTracks(tracks, loadDesktopSearchMatches(null, snapshot, "")))
        val blankPlaylists = loadDesktopSearchMatches(port, snapshot, "")
        assertEquals(1_205, prepareDesktopPagedPlaylistView(playlistInput,
            desktopSearchPlaylistIds(playlists, blankPlaylists)).matchCount) // Ignore a hidden retained draft.
        assertEquals("desktop_search_unavailable", loadDesktopSearchMatches(null, snapshot, "word").errorCode)
        assertEquals("desktop_search_invalid", loadDesktopSearchMatches(port, snapshot, "a".repeat(257)).errorCode)

        val malformed = listOf(
            DesktopTextSearchMatches("different", true), DesktopTextSearchMatches("word", false),
            DesktopTextSearchMatches("word", true, listOf("unknown")),
            DesktopTextSearchMatches("word", true, listOf(tracks.first().id, tracks.first().id)),
            DesktopTextSearchMatches("word", true, playlistIds = listOf("unknown")),
            DesktopTextSearchMatches("word", true, errorCode = "private://fixture"),
        )
        malformed.forEach { value ->
            port.answer = { _, _ -> value }
            val failed = loadDesktopSearchMatches(port, snapshot, "word")
            assertEquals("desktop_search_failed", failed.errorCode)
            assertTrue(desktopSearchTracks(tracks, failed).isEmpty())
            val failedPlaylists = prepareDesktopPagedPlaylistView(playlistInput, desktopSearchPlaylistIds(playlists, failed))
            assertEquals(0, failedPlaylists.matchCount)
            assertSame(selected, failedPlaylists.playlist)
        }
        port.answer = { _, _ -> error("private://fixture") }
        assertEquals("desktop_search_failed", loadDesktopSearchMatches(port, snapshot, "word").errorCode)
        // Reset still browses after a failure without calling the broken port.
        assertSame(tracks, desktopSearchTracks(tracks, loadDesktopSearchMatches(port, snapshot, "")))
        port.answer = { _, _ -> throw CancellationException("fixture_cancel") }
        try { loadDesktopSearchMatches(port, snapshot, "word"); fail("Cancellation must propagate") }
        catch (_: CancellationException) { }
    }

    private fun assertSearchCopy(): Unit {
        DesktopLanguage.entries.forEach { language ->
            listOf(DesktopText.SearchFailure, DesktopText.SearchUnavailable, DesktopText.SearchClosed,
                DesktopText.SearchInvalid, DesktopText.SearchRetry, DesktopText.SearchReset).forEach {
                assertTrue(it.inLanguage(language).isNotBlank())
            }
            listOf("desktop_search_failed", "desktop_search_unavailable", "desktop_search_closed",
                "desktop_search_invalid", "private://fixture").forEach { code ->
                val message = desktopSearchErrorText(code).inLanguage(language)
                assertTrue(message.isNotBlank())
                assertFalse(message.contains("private://"))
            }
        }
        assertEquals(DesktopText.SearchFailure, desktopSearchErrorText("private://fixture"))
    }

    private fun assertLifecycleTargets(): Unit {
        val unavailable = DesktopLifecycleState()
        assertFalse(desktopLifecycleCanQuit(unavailable))
        assertFalse(desktopLifecycleShowWindowHint(unavailable))
        assertFalse(desktopLifecycleShowClosing(unavailable))
        assertFalse(desktopLifecycleShowWindowHint(unavailable.copy(canHideWindow = true)))
        assertFalse(desktopLifecycleShowClosing(unavailable.copy(closing = true)))
        val state = DesktopLifecycleState(canHideWindow = true, available = true)
        assertTrue(desktopLifecycleCanQuit(state))
        assertTrue(desktopLifecycleCanQuit(state.copy(canHideWindow = false)))
        assertTrue(desktopLifecycleShowWindowHint(state))
        assertFalse(desktopLifecycleShowWindowHint(state.copy(canHideWindow = false)))
        assertFalse(desktopLifecycleShowClosing(state))
        assertTrue(desktopLifecycleShowClosing(state.copy(closing = true)))
        assertFalse(desktopLifecycleCanQuit(state.copy(closing = true)))
        val owner = DesktopLifecycleCallbacks()
        val target = DesktopLifecycleTarget(state, owner)
        assertTrue(target.canQuit(state, owner, target, true))
        // State admission uses latest scalar values; identity holders must still publish equal replacements.
        val equalState = state.copy()
        val latestState = desktopBrowseReferenceState(state)
        latestState.value = equalState
        assertSame(equalState, latestState.value)
        assertTrue(target.canQuit(latestState.value, owner, target, true))
        val equalOwner = owner.copy()
        assertEquals(owner, equalOwner)
        assertNotSame(owner, equalOwner)
        val latestOwner = desktopBrowseReferenceState(owner)
        latestOwner.value = equalOwner
        assertSame(equalOwner, latestOwner.value)
        assertFalse(target.canQuit(state, latestOwner.value, target, true))
        assertEquals(DesktopLifecycleOwnerKey(owner), DesktopLifecycleOwnerKey(owner))
        assertFalse(DesktopLifecycleOwnerKey(owner) == DesktopLifecycleOwnerKey(equalOwner))
        assertFalse(target.canQuit(state, owner, target, false))
        listOf(state.copy(closing = true), state.copy(available = false), state.copy(canHideWindow = false)).forEach { changed ->
            assertFalse(target.canQuit(changed, owner, target, true))
            val changedTarget = DesktopLifecycleTarget(changed, owner)
            assertEquals(desktopLifecycleCanQuit(changed), changedTarget.canQuit(changed, owner, changedTarget, true))
        }
        // A new target also represents Settings navigation A -> B -> A with unchanged scalar values.
        val returned = DesktopLifecycleTarget(state, owner)
        assertFalse(target.canQuit(state, owner, returned, true))
        assertTrue(returned.canQuit(state, owner, returned, true))
        target.retire()
        target.retire()
        assertFalse(target.canQuit(state, owner, target, true))
        returned.retire()
        assertFalse(returned.canQuit(state, owner, returned, true))
    }

    private fun assertLifecycleHandlers(): Unit {
        val calls = mutableListOf<String>()
        val state = DesktopLifecycleState(canHideWindow = true, available = true)
        val owner = DesktopLifecycleCallbacks(onQuit = { calls += "original" })
        val target = DesktopLifecycleTarget(state, owner)
        val latestState = desktopBrowseReferenceState(state)
        val latestOwner = desktopBrowseReferenceState(owner)
        val latestTarget = desktopBrowseReferenceState(target)
        var navigationCurrent = true
        val quit = target.quitHandler({ latestState.value }, { latestOwner.value }, { latestTarget.value }, { navigationCurrent })
        assertTrue(calls.isEmpty()) // Constructing a closure never starts shutdown.
        quit()
        assertEquals(listOf("original"), calls)
        latestState.value = state.copy(closing = true)
        repeat(2) { quit() }
        latestState.value = state.copy(available = false)
        quit()
        latestState.value = state.copy(canHideWindow = false)
        quit()
        val returned = DesktopLifecycleTarget(state, owner)
        latestState.value = state
        latestTarget.value = returned
        quit() // An equal scalar return cannot revive a retained earlier closure.
        target.retire()
        val returnedQuit = returned.quitHandler({ latestState.value }, { latestOwner.value }, { latestTarget.value }, { navigationCurrent })
        navigationCurrent = false
        returnedQuit()
        navigationCurrent = true
        val equalOwner = owner.copy()
        latestOwner.value = equalOwner
        returnedQuit()
        val equalTarget = DesktopLifecycleTarget(state, equalOwner)
        latestTarget.value = equalTarget
        val equalQuit = equalTarget.quitHandler({ latestState.value }, { latestOwner.value }, { latestTarget.value }, { navigationCurrent })
        equalQuit()
        assertEquals(listOf("original", "original"), calls)
        val replacement = DesktopLifecycleCallbacks(onQuit = { calls += "replacement" })
        latestOwner.value = replacement
        quit()
        returnedQuit()
        equalQuit() // Retained handlers cannot redirect to the replacement callback.
        val replacementTarget = DesktopLifecycleTarget(state, replacement)
        latestTarget.value = replacementTarget
        val replacementQuit = replacementTarget.quitHandler({ latestState.value }, { latestOwner.value },
            { latestTarget.value }, { navigationCurrent })
        replacementQuit()
        latestState.value = state.copy(closing = true)
        repeat(2) { replacementQuit() }
        replacementTarget.retire()
        latestState.value = state
        replacementQuit()
        returned.retire()
        equalTarget.retire()
        latestOwner.value = owner
        latestTarget.value = target
        quit() // Restoring every old reference cannot revive a disposed target.
        assertEquals(listOf("original", "original", "replacement"), calls)
    }

    private fun assertLifecycleCopy(): Unit {
        DesktopLanguage.entries.forEach { language ->
            DesktopLifecycleText.entries.forEach { text ->
                assertTrue(text.label(language).isNotBlank())
                assertFalse(text.label(language).contains("{"))
            }
            assertTrue(DesktopLifecycleText.WindowCloseHint.label(language).contains(DesktopLifecycleText.Quit.label(language)))
            assertTrue(DesktopLifecycleText.Closing.label(language).contains("AutPlay"))
        }
        assertEquals("Settings", DesktopLifecycleText.Settings.label(DesktopLanguage.English))
        assertEquals("Настройки", DesktopLifecycleText.Settings.label(DesktopLanguage.Russian))
        assertEquals("Show window", DesktopLifecycleText.ShowWindow.label(DesktopLanguage.English))
        assertEquals("Показать окно", DesktopLifecycleText.ShowWindow.label(DesktopLanguage.Russian))
        assertEquals("Close window", DesktopLifecycleText.CloseWindow.label(DesktopLanguage.English))
        assertEquals("Закрыть окно", DesktopLifecycleText.CloseWindow.label(DesktopLanguage.Russian))
        assertEquals("Quit AutPlay", DesktopLifecycleText.Quit.label(DesktopLanguage.English))
        assertEquals("Выйти из AutPlay", DesktopLifecycleText.Quit.label(DesktopLanguage.Russian))
        assertEquals("Closing AutPlay…", DesktopLifecycleText.Closing.label(DesktopLanguage.English))
        assertEquals("Завершение работы AutPlay…", DesktopLifecycleText.Closing.label(DesktopLanguage.Russian))
        assertEquals("Closing the window hides it; playback continues. Quit AutPlay ends the app and playback.",
            DesktopLifecycleText.WindowCloseHint.label(DesktopLanguage.English))
        assertEquals("Закрытие окна скрывает его; воспроизведение продолжается. «Выйти из AutPlay» завершает приложение и воспроизведение.",
            DesktopLifecycleText.WindowCloseHint.label(DesktopLanguage.Russian))
    }

    private fun assertSmoothTransitionTargets(): Unit {
        val owner = DesktopSettingsCallbacks()
        val scope = DesktopSmoothTransitionsScope(false, writable = true, busy = false)
        val target = DesktopSmoothTransitionsTarget(scope, owner)
        assertTrue(target.canChange(true, scope, owner, target))
        assertFalse(target.canChange(false, scope, owner, target))
        val equalOwner = owner.copy()
        assertEquals(owner, equalOwner)
        assertNotSame(owner, equalOwner)
        assertFalse(target.canChange(true, scope, equalOwner, target))
        assertFalse(DesktopSettingsOwnerKey(owner) == DesktopSettingsOwnerKey(equalOwner))
        assertEquals(DesktopSettingsOwnerKey(owner), DesktopSettingsOwnerKey(owner))
        listOf(scope.copy(value = true), scope.copy(value = null), scope.copy(writable = false),
            scope.copy(busy = true)).forEach { changed ->
            assertFalse(target.canChange(true, changed, owner, target))
            val disabled = DesktopSmoothTransitionsTarget(changed, owner)
            if (changed.value == null || !changed.writable || changed.busy) {
                assertFalse(disabled.canChange(true, changed, owner, disabled))
            }
        }
        // Equal A after B has a new lifetime, so an old event cannot become live again.
        val returned = DesktopSmoothTransitionsTarget(scope.copy(), owner)
        assertFalse(target.canChange(true, scope, owner, returned))
        assertTrue(returned.canChange(true, scope, owner, returned))
        target.retire()
        assertFalse(target.canChange(true, scope, owner, target))
        returned.retire()
        assertFalse(returned.canChange(true, scope, owner, returned))
        val calls = mutableListOf<Boolean>()
        val dispatchOwner = DesktopSettingsCallbacks(onSmoothTrackTransitions = { calls += it })
        var latestScope = scope
        var latestOwner = dispatchOwner
        val dispatchTarget = DesktopSmoothTransitionsTarget(scope, dispatchOwner)
        var latestTarget = dispatchTarget
        val handler = dispatchTarget.changeHandler({ latestScope }, { latestOwner }, { latestTarget })
        handler(false)
        handler(true)
        assertEquals(listOf(true), calls)
        latestScope = scope.copy(busy = true)
        handler(true)
        latestScope = scope.copy(writable = false)
        handler(true)
        latestScope = scope.copy(value = null)
        handler(true)
        latestScope = scope
        latestOwner = dispatchOwner.copy()
        assertEquals(dispatchOwner, latestOwner)
        handler(true)
        latestOwner = dispatchOwner
        latestTarget = DesktopSmoothTransitionsTarget(scope, dispatchOwner)
        handler(true)
        dispatchTarget.retire()
        latestTarget = dispatchTarget
        handler(true)
        assertEquals(listOf(true), calls)
    }

    private fun assertTrackVisualTransitions(): Unit {
        val ready = readyState()
        val identity = desktopTrackVisualIdentity(ready)
        assertEquals(DesktopTrackVisualIdentity("snapshot", "entry-current"), identity)
        assertEquals(identity, desktopTrackVisualIdentity(ready.copy(positionMs = 99_999, title = "Edited", artist = "New")))
        assertEquals(null, desktopTrackVisualIdentity(ready.copy(mediaId = null)))
        assertEquals(null, desktopTrackVisualIdentity(ready.copy(mediaId = "different")))
        val loaded = ready.context as ActiveQueueContext.Loaded
        assertEquals(null, desktopTrackVisualIdentity(ready.copy(context = loaded.copy(snapshotId = ""))))
        assertEquals(DesktopTrackVisualIdentity("other-queue", "entry-current"),
            desktopTrackVisualIdentity(ready.copy(context = loaded.copy(snapshotId = "other-queue"))))
        assertEquals(null, desktopTrackVisualIdentity(PlaybackPresentationState()))
        val a = DesktopTrackVisualFrame(identity, "A")
        val b = DesktopTrackVisualFrame(DesktopTrackVisualIdentity("snapshot", "entry-next"), "B")
        val first = advanceDesktopTrackVisualFrames(null, a, DesktopSection.Player, 1, true, true)
        assertEquals(1, first.frameCount)
        assertEquals(null, first.transition)
        assertEquals(1, advanceDesktopTrackVisualFrames(first, a.copy(value = "Edited A"),
            DesktopSection.Player, 1, true, true).frameCount)
        // Different occurrences of the same track still change visual identity.
        assertEquals(2, advanceDesktopTrackVisualFrames(first, b.copy(value = a.value),
            DesktopSection.Player, 1, true, true).frameCount)
        val second = advanceDesktopTrackVisualFrames(first, b, DesktopSection.Player, 1, true, true)
        assertEquals(2, second.frameCount)
        assertEquals(a, second.outgoing)
        assertEquals(b, second.current)
        val transition = checkNotNull(second.transition)
        val refreshed = advanceDesktopTrackVisualFrames(second, b.copy(value = "B metadata"), DesktopSection.Player, 1, true, true)
        assertSame(transition, refreshed.transition)
        assertEquals(a, refreshed.outgoing)
        assertEquals("B metadata", refreshed.current.value)
        val returned = advanceDesktopTrackVisualFrames(refreshed, a, DesktopSection.Player, 1, true, true)
        assertEquals(2, returned.frameCount)
        assertEquals(refreshed.current, returned.outgoing)
        assertNotSame(transition, returned.transition)
        assertSame(returned, finishDesktopTrackVisualTransition(returned, transition))
        val finished = finishDesktopTrackVisualTransition(returned, checkNotNull(returned.transition))
        assertEquals(1, finished.frameCount)
        assertEquals(a, finished.current)
        assertEquals(null, finished.transition)
        listOf(
            advanceDesktopTrackVisualFrames(second, b, DesktopSection.Player, 1, true, false),
            advanceDesktopTrackVisualFrames(second, b, DesktopSection.Player, 2, true, true),
            advanceDesktopTrackVisualFrames(second, b, DesktopSection.Home, 1, true, true),
            advanceDesktopTrackVisualFrames(second, b, DesktopSection.Player, 1, false, true),
            advanceDesktopTrackVisualFrames(second, DesktopTrackVisualFrame(null, "Empty"), DesktopSection.Player, 1, true, true),
        ).forEach { snapped ->
            assertEquals(1, snapped.frameCount)
            assertEquals(null, snapped.outgoing)
            assertEquals(null, snapped.transition)
        }
        val retired = advanceDesktopTrackVisualFrames(second, b, DesktopSection.Player, 1, false, true)
        assertEquals(1, advanceDesktopTrackVisualFrames(retired, a, DesktopSection.Player, 1, true, true).frameCount)
        var latest = first
        repeat(100) { index ->
            val frame = if (index % 2 == 0) b else a
            latest = advanceDesktopTrackVisualFrames(latest, frame, DesktopSection.Player, 1, true, true)
            assertEquals(2, latest.frameCount)
            assertEquals(frame, latest.current)
        }
    }

    private fun assertMetadataAdmission(): Unit {
        val track = DesktopTrack("metadata-track", "Local title", "Local artist", 1_000, "a".repeat(64), 100)
        val snapshot = DesktopLibrarySnapshot(tracks = listOf(track))
        var selected = track.id
        var revision = 1L
        var navigation = 0L
        val input = DesktopTrackInput(snapshot, track.id, null, 1) {
            selected == track.id && revision == 1L && navigation == 0L
        }
        val view = DesktopTrackView(input, track)
        val target = DesktopMetadataTarget(input, track)
        val baseline = DesktopTrackTextBaseline(track.id, track.title, track.artist, track.sha256, track.byteSize)
        val embedded = DesktopEmbeddedMetadata("AVAILABLE", "ID3V23", "Embedded title", "Embedded artist", "Album evidence")
        val provenance = DesktopTrackTextProvenance(baseline, 7,
            title = DesktopTrackFieldProvenance(DesktopTrackTextOrigin.USER))
        val preview = DesktopMetadataPreview("inspect-1", baseline, embedded, "Embedded title", "Embedded artist", provenance)
        val state = DesktopMetadataState(preview.id, track.id, preview = preview)
        assertSame(provenance, checkNotNull(preview.application).expected)
        assertEquals("Embedded title", preview.application?.title)
        assertEquals("Embedded artist", preview.application?.artist)
        DesktopEmbeddedTextFormat.entries.forEach { format ->
            val formatted = preview.copy(embedded = embedded.copy(format = format.name))
            assertTrue(formatted.canApply)
            assertEquals(format, formatted.application?.format)
            assertEquals(DesktopTrackFieldProvenance(DesktopTrackTextOrigin.EMBEDDED, format), formatted.proposedTitleProvenance)
        }
        listOf("UNKNOWN", "id3v23", "ID3V23 ", "FUTURE", "private://format").forEach { format ->
            val unknown = preview.copy(embedded = embedded.copy(format = format))
            assertFalse(unknown.canApply)
            assertEquals(null, unknown.application)
            assertEquals(null, unknown.proposedTitleProvenance)
        }
        val mismatched = preview.copy(provenance = provenance.copy(baseline = baseline.copy(title = "Other stored title")))
        listOf(preview.copy(provenance = null), mismatched,
            preview.copy(provenance = provenance.copy(metadataRevision = -1))).forEach { unbound ->
            assertFalse(unbound.canApply)
            assertEquals(null, unbound.application)
        }
        assertEquals(null, mismatched.proposedTitleProvenance)
        val partial = preview.copy(embedded = embedded.copy(title = null, artist = ""),
            proposedTitle = baseline.title, proposedArtist = "")
        assertTrue(partial.canApply)
        assertEquals(null, partial.application?.title)
        assertEquals("", partial.application?.artist)
        assertEquals(provenance.title, partial.proposedTitleProvenance)
        assertEquals(DesktopTrackFieldProvenance(DesktopTrackTextOrigin.EMBEDDED, DesktopEmbeddedTextFormat.ID3V23),
            partial.proposedArtistProvenance)
        val sameTitle = partial.copy(embedded = partial.embedded.copy(title = baseline.title))
        assertEquals(provenance.title, sameTitle.proposedTitleProvenance)
        val unchanged = preview.copy(embedded = embedded.copy(title = baseline.title, artist = baseline.artist),
            proposedTitle = baseline.title, proposedArtist = baseline.artist)
        assertFalse(unchanged.canApply)
        assertEquals(provenance.title, unchanged.proposedTitleProvenance)
        assertEquals(provenance.artist, unchanged.proposedArtistProvenance)
        assertTrue(desktopMetadataCanInspect(input, view, DesktopMetadataState(), false))
        assertTrue(target.canApply(input, view, state, preview, false))
        assertFalse(target.canApply(input, view, state, preview.copy(), false))
        assertFalse(target.canApply(input, view, state.copy(preview = preview.copy(id = "inspect-2")), preview, false))
        assertFalse(target.canApply(input, view, state.copy(requestId = "other-read"), preview, false))
        assertFalse(target.canApply(input, view, state.copy(trackId = "other-track"), preview, false))
        assertFalse(target.canApply(input, view, state, preview, true))
        assertFalse(desktopMetadataCanInspect(input, view, state, true))
        assertEquals(preview.id, desktopMetadataDismissId(state))
        assertEquals(null, desktopMetadataCancelId(state))
        val reading = state.copy(busy = true, preview = null)
        assertFalse(desktopMetadataCanInspect(input, view, reading, false))
        assertFalse(target.canApply(input, view, reading, preview, false))
        assertEquals(preview.id, desktopMetadataCancelId(reading))
        assertEquals(null, desktopMetadataDismissId(reading))
        listOf(state.copy(applying = true), reading.copy(applying = true)).forEach { applying ->
            assertFalse(desktopMetadataCanInspect(input, view, applying, false))
            assertFalse(target.canApply(input, view, applying, preview, false))
            assertEquals(null, desktopMetadataCancelId(applying))
            assertEquals(null, desktopMetadataDismissId(applying))
        }
        val failed = state.copy(errorCode = "desktop_metadata_conflict")
        assertTrue(failed.preview === preview)
        assertFalse(target.canApply(input, view, failed, preview, false))
        assertTrue(desktopMetadataCanInspect(input, view, failed, false))
        assertEquals(preview.id, desktopMetadataDismissId(failed))
        assertEquals(preview.id, desktopMetadataDismissId(failed.copy(preview = null)))
        assertEquals(preview.id, desktopMetadataDismissId(failed.copy(errorCode = "desktop_snapshot_unavailable")))
        assertEquals(null, desktopMetadataDismissId(DesktopMetadataState()))
        assertFalse(desktopMetadataCanInspect(input, DesktopTrackView(), state, false))
        assertFalse(target.canApply(input, DesktopTrackView(), state, preview, false))
        assertFalse(target.canApply(input, DesktopTrackView(input), state, preview, false))
        assertFalse(desktopMetadataCanInspect(DesktopTrackInput(null, track.id, null), view, state, false))
        listOf(track.copy(id = "other"), track.copy(title = "External edit"), track.copy(artist = "Other artist"),
            track.copy(sha256 = "b".repeat(64)), track.copy(byteSize = 101)).forEach { changed ->
            val updated = DesktopTrackInput(snapshot.copy(tracks = listOf(changed)), track.id, null, 1)
            assertFalse(target.canApply(updated, DesktopTrackView(updated, changed), state, preview, false))
        }
        // Duration cannot write labels. Its metadata-revision conflict is enforced by the host/store CAS.
        val nativeDuration = track.copy(durationMs = 2_000)
        val refreshed = DesktopTrackInput(snapshot.copy(tracks = listOf(nativeDuration)), track.id, null, 1)
        assertTrue(target.canApply(refreshed, DesktopTrackView(refreshed, nativeDuration), state, preview, false))
        assertEquals("Local title", nativeDuration.title)
        assertEquals("Local artist", nativeDuration.artist)
        assertEquals("Embedded title", preview.proposedTitle)
        listOf("NO_TAGS", "UNSUPPORTED", "INVALID", "UNAVAILABLE", "FUTURE").forEach { status ->
            val refusedPreview = preview.copy(embedded = embedded.copy(status = status))
            assertFalse(target.canApply(input, view, state.copy(preview = refusedPreview), refusedPreview, false))
        }
        listOf(preview.copy(proposedTitle = " "), preview.copy(proposedTitle = "x".repeat(4097)),
            preview.copy(proposedArtist = "\u0000"), preview.copy(proposedTitle = "\uD800"),
            preview.copy(proposedTitle = track.title, proposedArtist = track.artist)).forEach { refusedPreview ->
            assertFalse(target.canApply(input, view, state.copy(preview = refusedPreview), refusedPreview, false))
        }
        // A refreshed baseline is never substituted into the already inspected selection.
        val edited = track.copy(title = "Edited since read")
        val changedInput = DesktopTrackInput(snapshot.copy(tracks = listOf(edited)), track.id, null, 1)
        val rebased = preview.copy(baseline = baseline.copy(title = edited.title))
        assertFalse(target.canApply(changedInput, DesktopTrackView(changedInput, edited), state.copy(preview = rebased), rebased, false))
        selected = "other"
        assertFalse(desktopMetadataCanInspect(input, view, state, false))
        assertFalse(target.canApply(input, view, state, preview, false))
        selected = track.id
        revision = 3
        val reopened = DesktopTrackInput(snapshot, track.id, null, 3)
        assertFalse(target.canApply(reopened, DesktopTrackView(reopened, track), state, preview, false))
        revision = 1
        navigation = 2
        assertFalse(target.canApply(input, view, state, preview, false))
        val calls = mutableListOf<String>()
        val callbacks = DesktopMetadataCallbacks(onInspect = { calls += "inspect:$it" }, onCancel = { calls += "cancel:$it" },
            onApply = { calls += "apply:$it" }, onDismiss = { calls += "dismiss:$it" })
        callbacks.onInspect(track.id)
        desktopMetadataCancelId(reading)?.let(callbacks.onCancel)
        callbacks.onApply(preview.id)
        desktopMetadataDismissId(failed)?.let(callbacks.onDismiss)
        assertEquals(listOf("inspect:${track.id}", "cancel:${preview.id}", "apply:${preview.id}", "dismiss:${preview.id}"), calls)
    }

    private fun assertMetadataArtworkBounds(): Unit {
        fun header(width: Int, height: Int): ByteArray {
            val bytes = ByteArray(33)
            intArrayOf(137, 80, 78, 71, 13, 10, 26, 10, 0, 0, 0, 13, 73, 72, 68, 82).forEachIndexed { index, byte ->
                bytes[index] = byte.toByte()
            }
            listOf(16 to width, 20 to height).forEach { (offset, dimension) ->
                for (index in 0..3) bytes[offset + index] = (dimension ushr (24 - index * 8)).toByte()
            }
            return bytes
        }
        val bytes = header(512, 512)
        val artwork = DesktopArtwork("a".repeat(64), bytes, 512, 512)
        bytes[0] = 0
        assertEquals(137, artwork.copyPngBytes()[0].toInt() and 255)
        val copied = artwork.copyPngBytes()
        copied[0] = 0
        assertEquals(137, artwork.copyPngBytes()[0].toInt() and 255)
        assertTrue(desktopArtworkHeaderMatches(artwork, artwork.copyPngBytes()))
        assertFalse(desktopArtworkHeaderMatches(artwork, copied))
        listOf(0, 513, Int.MAX_VALUE).forEach { dimension ->
            assertFalse(desktopArtworkHeaderMatches(artwork, header(dimension, 512)))
            assertFalse(desktopArtworkHeaderMatches(artwork, header(512, dimension)))
        }
        val wrongChunk = artwork.copyPngBytes().also { it[12] = 0 }
        val wrongLength = artwork.copyPngBytes().also { it[11] = 12 }
        assertFalse(desktopArtworkHeaderMatches(artwork, wrongChunk))
        assertFalse(desktopArtworkHeaderMatches(artwork, wrongLength))
        assertFalse(desktopArtworkHeaderMatches(artwork, ByteArray(32)))
        assertFalse(desktopArtworkHeaderMatches(artwork, ByteArray(DesktopEmbeddedMetadataLimits.MAX_THUMBNAIL_BYTES + 1)))
        fun rejected(payload: ByteArray, width: Int = 1, height: Int = 1, hash: String = "a".repeat(64)) {
            try { DesktopArtwork(hash, payload, width, height); fail("Invalid artwork must be rejected") }
            catch (_: IllegalArgumentException) { }
        }
        rejected(byteArrayOf())
        rejected(ByteArray(DesktopEmbeddedMetadataLimits.MAX_THUMBNAIL_BYTES + 1))
        rejected(byteArrayOf(1), width = 0)
        rejected(byteArrayOf(1), height = 513)
        rejected(byteArrayOf(1), hash = "A".repeat(64))
        val maximumPayload = header(512, 512).copyOf(DesktopEmbeddedMetadataLimits.MAX_THUMBNAIL_BYTES)
        val maximum = DesktopArtwork("b".repeat(64), maximumPayload, 512, 512)
        assertTrue(desktopArtworkHeaderMatches(maximum, maximum.copyPngBytes()))
        val identity = DesktopArtworkRenderIdentity("track-a", artwork)
        assertTrue(identity.matches("track-a", artwork))
        assertFalse(identity.matches("track-b", artwork))
        assertFalse(identity.matches("track-a", null))
        assertFalse(identity.matches(null, artwork))
        assertFalse(identity.matches("track-a", DesktopArtwork(artwork.sha256, artwork.copyPngBytes(), 512, 512)))
        assertTrue(DesktopMetadataState().artworks.isEmpty())
        assertEquals(16, DesktopEmbeddedMetadataLimits.MAX_CACHE_ENTRIES)
        assertEquals(16 * 1024 * 1024, DesktopEmbeddedMetadataLimits.MAX_CACHE_BYTES)
    }

    private fun assertMetadataCopy(): Unit {
        assertEquals("MP4/M4A / список встроенных тегов", desktopMetadataFormat("MP4", DesktopLanguage.Russian))
        assertEquals("MP4/M4A / embedded item list", desktopMetadataFormat("MP4", DesktopLanguage.English))
        DesktopLanguage.entries.forEach { language ->
            DesktopMetadataText.entries.forEach { assertTrue(it.label(language).isNotBlank()) }
            assertEquals(DesktopMetadataText.UnknownOrigin.label(language), desktopMetadataProvenance(null, language))
            assertEquals(DesktopMetadataText.UnknownOrigin.label(language),
                desktopMetadataProvenance(DesktopTrackFieldProvenance(), language))
            assertEquals(DesktopMetadataText.UserOrigin.label(language),
                desktopMetadataProvenance(DesktopTrackFieldProvenance(DesktopTrackTextOrigin.USER), language))
            DesktopEmbeddedTextFormat.entries.forEach { format ->
                assertEquals("${DesktopMetadataText.EmbeddedOrigin.label(language)}: ${desktopMetadataFormat(format.name, language)}",
                    desktopMetadataProvenance(DesktopTrackFieldProvenance(DesktopTrackTextOrigin.EMBEDDED, format), language))
            }
            assertEquals("ID3v2.3", desktopMetadataFormat("ID3V23", language))
            assertEquals("ID3v2.4", desktopMetadataFormat("ID3V24", language))
            assertEquals("FLAC / Vorbis Comments", desktopMetadataFormat("FLAC", language))
            assertEquals(DesktopMetadataText.Mp4Format.label(language), desktopMetadataFormat("MP4", language))
            assertTrue(desktopMetadataFormat("MP4", language).contains("MP4/M4A"))
            listOf(null, "UNKNOWN", "private://path", "<script>instruction</script>").forEach { format ->
                assertEquals(DesktopMetadataText.UnknownFormat.label(language), desktopMetadataFormat(format, language))
            }
            listOf("AVAILABLE" to DesktopMetadataText.Available, "NO_TAGS" to DesktopMetadataText.NoTags,
                "UNSUPPORTED" to DesktopMetadataText.Unsupported, "INVALID" to DesktopMetadataText.Invalid,
                "UNAVAILABLE" to DesktopMetadataText.Unavailable, "FUTURE" to DesktopMetadataText.Unavailable,
                null to DesktopMetadataText.Unavailable).forEach { (status, copy) ->
                assertEquals(copy.label(language), desktopMetadataStatus(status, language))
            }
            listOf(null, "\u0000", "\uD800", "\uDC00", "x".repeat(4097)).forEach { text ->
                assertEquals(DesktopMetadataText.Missing.label(language), desktopMetadataSafeText(text, language))
            }
            listOf("", "  Exact\ntext  ", "<a href='file:///raw'>plain text</a>", "https://example.test/", "x".repeat(4096)).forEach { text ->
                assertEquals(text, desktopMetadataSafeText(text, language))
            }
        }
        assertTrue(DesktopMetadataText.Current.english.contains("at inspection"))
        assertTrue(DesktopMetadataText.Current.russian.contains("на момент чтения"))
        assertTrue(DesktopMetadataText.UnknownOrigin.english.contains("Unknown"))
        assertTrue(DesktopMetadataText.UnknownOrigin.russian.contains("Неизвестно"))
        assertTrue(DesktopMetadataText.ProvenanceHint.english.contains("source access permission"))
        assertTrue(DesktopMetadataText.Changed.english.contains("metadata revision"))
        assertTrue(DesktopMetadataText.Album.english.contains("information only"))
        assertTrue(DesktopMetadataText.CoverHint.english.contains("restart or eviction"))
        assertTrue(DesktopMetadataText.NoCover.english.contains("process cache"))
        assertTrue(DesktopMetadataText.Hint.english.contains("only local title and artist"))
    }

    private fun assertHomeCommandPolicies(): Unit {
        val track = DesktopTrack("retained", "Retained", "Artist", 1_000, "a".repeat(64), 1, collectionState = "REMOVED")
        val queue = DesktopQueue("snapshot", listOf(DesktopQueueEntry("entry-current", track.id)), "entry-current",
            positionMs = 320, sessionId = "listen", playedMs = 120)
        val library = DesktopLibrarySnapshot(tracks = listOf(track), queue = queue)
        val input = DesktopHomeInput(library)
        val view = prepareDesktopHomeView(input)
        val paused = readyState().copy(localTrackRefId = track.id,
            playbackStatus = app.autplay.playback.presentation.PlaybackStatus.Ready)
        val target = DesktopHomeContinuation(input, view, paused)
        assertTrue(desktopHomeCanContinue(input, view, paused, true, false))
        assertTrue(target.canAct(input, view, paused.copy(positionMs = 600), true, false, true))
        assertFalse(desktopTrackIsActive(view.currentTrack)) // Resume and a new selection have different admission rules.
        assertFalse(target.canAct(input, view, paused.copy(isPlaying = true), true, false, true))
        assertFalse(target.canAct(input, view, paused, false, false, true))
        assertFalse(target.canAct(input, view, paused, true, true, true))
        assertFalse(target.canAct(input, view, paused, true, false, false))
        assertFalse(target.canAct(DesktopHomeInput(library.copy()), view, paused, true, false, true))
        assertFalse(target.canAct(input, prepareDesktopHomeView(input), paused, true, false, true))
        val continueRequests = mutableListOf<Triple<String, String, String?>>()
        val continueCallbacks = DesktopLibraryCallbacks(onContinueQueue = { queueId, entryId, sessionId ->
            continueRequests += Triple(queueId, entryId, sessionId)
        })
        target.continueQueue(input, view, paused, true, false, true, continueCallbacks)
        assertEquals(listOf(Triple("snapshot", "entry-current", "listen")), continueRequests)
        // A queued owner change must receive the displayed identities, never an unscoped Play command.
        listOf(queue.copy(id = "replacement"), queue.copy(currentEntryId = "next-entry"),
            queue.copy(sessionId = "next-listen")).forEach { replacement ->
            val nextInput = DesktopHomeInput(library.copy(queue = replacement))
            target.continueQueue(nextInput, prepareDesktopHomeView(nextInput), paused, true, false, true, continueCallbacks)
        }
        target.continueQueue(input, view, paused.copy(isPlaying = true), true, false, true, continueCallbacks)
        target.continueQueue(input, view, paused, true, false, false, continueCallbacks)
        target.continueQueue(input, view, paused, true, true, true, continueCallbacks)
        assertEquals(1, continueRequests.size)
        val withoutSessionInput = DesktopHomeInput(library.copy(queue = queue.copy(sessionId = null)))
        val withoutSessionView = prepareDesktopHomeView(withoutSessionInput)
        DesktopHomeContinuation(withoutSessionInput, withoutSessionView, paused)
            .continueQueue(withoutSessionInput, withoutSessionView, paused, true, false, true, continueCallbacks)
        assertEquals(Triple("snapshot", "entry-current", null), continueRequests.last())
        listOf(paused.copy(mediaId = "other"), paused.copy(localTrackRefId = "other"),
            paused.copy(connection = PlaybackConnectionState.Disconnected),
            paused.copy(context = ActiveQueueContext.Loaded("replacement", "entry-current", "LIBRARY")),
            paused.copy(context = ActiveQueueContext.Loaded("snapshot", "other", "LIBRARY")),
            paused.copy(context = ActiveQueueContext.Loaded("snapshot", "entry-current", "WAVE")),
            paused.copy(playbackStatus = app.autplay.playback.presentation.PlaybackStatus.Ended),
            paused.copy(controls = PlaybackControlGate.Locked(PlaybackControlLockReason.COMMAND_UNAVAILABLE))).forEach {
            assertFalse(target.canAct(input, view, it, true, false, true))
        }
        assertFalse(desktopHomeCanContinue(DesktopHomeInput(null), DesktopHomeView(), paused, true, false))
        val unknown = DesktopHomeInput(library.copy(queue = queue.copy(repeatMode = "FUTURE")))
        assertFalse(desktopHomeCanContinue(unknown, prepareDesktopHomeView(unknown), paused, true, false))
        assertTrue(library.queue === queue)
        assertEquals(320L, queue.positionMs)
        assertEquals("listen", queue.sessionId)
        assertEquals(120L, queue.playedMs)
        assertTrue(desktopHomeCanImport(DesktopLibrarySnapshot(), false, false))
        assertFalse(desktopHomeCanImport(null, false, false))
        assertFalse(desktopHomeCanImport(library, true, false))
        assertFalse(desktopHomeCanImport(library, false, true))
        // Restart refusal gates new imports, not cancellation of an already active import.
        assertTrue(desktopHomeCanCancelImport(library, true))
        assertFalse(desktopHomeCanCancelImport(library, false))
        assertFalse(desktopHomeCanCancelImport(null, true))
    }

    private fun assertHistoryRequestPolicies(): Unit {
        val state = DesktopHistoryState(nextCursor = DesktopHistoryCursor(100, "cursor"), initialized = true, revision = 7)
        val library = DesktopLibrarySnapshot()
        val input = DesktopHistoryInput(state, library)
        assertTrue(input.canRequest(DesktopHistoryInput(state, library), true, false))
        assertTrue(input.canLoadMore(input, true, false))
        assertFalse(input.canRetry(input, true, false))
        listOf(DesktopHistoryInput(state.copy(), library), DesktopHistoryInput(state.copy(revision = 8), library),
            DesktopHistoryInput(state, library.copy()), DesktopHistoryInput(state, null)).forEach {
            assertFalse(input.canRequest(it, true, false))
            assertFalse(input.canLoadMore(it, true, false))
        }
        assertFalse(input.canRequest(input, false, false))
        assertFalse(input.canLoadMore(input, false, false))
        assertFalse(input.canRequest(input, true, true))
        val loading = DesktopHistoryInput(state.copy(loading = true), library)
        assertFalse(loading.canRequest(loading, true, false))
        assertFalse(loading.canLoadMore(loading, true, false))
        val failed = DesktopHistoryInput(state.copy(errorCode = "private://failure"), library)
        assertTrue(failed.canRetry(failed, true, false))
        assertFalse(failed.canLoadMore(failed, true, false))
        assertFalse(failed.canRetry(input, true, false))
        assertFalse(failed.canRetry(failed, false, false))
        assertFalse(failed.canRetry(failed, true, true))
        val end = DesktopHistoryInput(state.copy(nextCursor = null), library)
        assertFalse(end.canLoadMore(end, true, false))
        val unopened = DesktopHistoryInput(DesktopHistoryState(), null)
        assertTrue(unopened.canRequest(unopened, true, false)) // Read-only history is independent of library writes.
        assertFalse(unopened.canLoadMore(unopened, true, false))
        assertEquals(DesktopText.HistoryLoading, desktopHistoryStatus(state.copy(loading = true), true, false, true))
        assertEquals(DesktopText.HistoryLoading, desktopHistoryStatus(state, false, false, false))
        assertEquals(DesktopText.HistoryFailure, desktopHistoryStatus(failed.state, true, false, false))
        assertEquals(null, desktopHistoryStatus(failed.state, true, true, false))
        assertEquals(DesktopText.HistoryNotLoaded, desktopHistoryStatus(unopened.state, true, false, true))
        assertEquals(DesktopText.EmptyHistory, desktopHistoryStatus(state, true, false, true))
        assertEquals(null, desktopHistoryStatus(state, true, false, false))
    }

    private fun assertPlaylistSearchRoutes(): Unit {
        val beyond = DesktopPlaylist("beyond-cap", "Музыка \uD83C\uDFB5", listOf(DesktopPlaylistEntry("duplicate-1", "track"),
            DesktopPlaylistEntry("duplicate-2", "track")), description = "  Evening \uD83C\uDFB5  ")
        val playlists = List(1_100) { DesktopPlaylist("prefix-$it", "A $it", emptyList()) } + beyond
        val library = DesktopLibrarySnapshot(playlists = playlists)
        val unfiltered = prepareDesktopPlaylistView(DesktopPlaylistInput(library, beyond.id), 1)
        assertEquals(1_000, unfiltered.choices.size)
        assertEquals(1_101, unfiltered.matchCount)
        assertEquals(beyond.id, unfiltered.playlist?.id)
        val query = DesktopPlaylistInput(library, beyond.id, 5, "eVENing")
        val filtered = prepareDesktopPlaylistView(query, 1)
        assertEquals(listOf(beyond.id), filtered.choices.map { it.id })
        assertEquals(beyond.id, filtered.playlist?.id)
        assertEquals(2, filtered.playlist?.entries?.size)
        assertEquals(listOf("track", "track"), prepareDesktopPlaylistView(query, 10).playIds)
        assertFalse(query == DesktopPlaylistInput(library, beyond.id, 5, "another"))
        assertFalse(query == DesktopPlaylistInput(library, beyond.id, 7, "eVENing"))
        assertFalse(query == DesktopPlaylistInput(library.copy(), beyond.id, 5, "eVENing"))
        assertEquals(listOf(beyond.id), prepareDesktopPlaylistView(DesktopPlaylistInput(library, null, query = "музыка"), 10).choices.map { it.id })
        assertEquals(listOf(beyond.id), prepareDesktopPlaylistView(DesktopPlaylistInput(library, null, query = "\uD83C\uDFB5"), 10).choices.map { it.id })
        val noMatches = prepareDesktopPlaylistView(DesktopPlaylistInput(library, beyond.id, query = "missing"), 10)
        assertTrue(noMatches.choices.isEmpty())
        assertEquals(beyond.id, noMatches.playlist?.id) // Search never silently replaces a stable selected ID.
        val ties = library.copy(playlists = listOf(beyond.copy(id = "z", name = "same"), beyond.copy(id = "a", name = "SAME")))
        assertEquals(listOf("a", "z"), prepareDesktopPlaylistView(DesktopPlaylistInput(ties, null), 10).choices.map { it.id })
        assertEquals("a".repeat(256), desktopSearchQuery("a".repeat(257)))
        assertEquals("a".repeat(254) + "\uD83C\uDFB5", desktopSearchQuery("a".repeat(254) + "\uD83C\uDFB5" + "z"))
        assertEquals("a".repeat(255), desktopSearchQuery("a".repeat(255) + "\uD83C\uDFB5"))
        assertEquals("  Музыка  ", desktopSearchQuery("  Музыка  "))
        var checkpoints = 0
        try {
            prepareDesktopPlaylistView(DesktopPlaylistInput(library, null, query = "missing"), 10) {
                if (++checkpoints == 3) throw CancellationException("playlist_search_cancelled")
            }
            fail("Playlist search cancellation must propagate")
        } catch (expected: CancellationException) {
            assertEquals("playlist_search_cancelled", expected.message)
        }
    }

    private fun assertHomeAndHistoryPreparation(): Unit {
        val active = DesktopTrack("active", "Title", "Artist", 1_000, "a".repeat(64), 1)
        val removed = active.copy(id = "removed", collectionState = "REMOVED")
        val events = List(1_001) { index -> DesktopListeningEvent("event-$index", "entry", when (index % 3) {
            0 -> active.id; 1 -> removed.id; else -> "missing"
        }, 2_000L - index, 100, 100, true) }
        val preview = events.first().copy(id = "preview-only")
        val library = DesktopLibrarySnapshot(tracks = listOf(active, removed), history = listOf(preview))
        val state = DesktopHistoryState(events = events, initialized = true, revision = 9, newerItemsHidden = true)
        val input = DesktopHistoryInput(state, library)
        val view = prepareDesktopHistoryView(input)
        assertEquals(DesktopHistoryLimits.MAX_VISIBLE_EVENTS, view.rows.size)
        assertEquals(events.take(1_000).map { it.id }, view.rows.map { it.event.id })
        assertFalse(view.rows.any { it.event.id == "preview-only" })
        assertTrue(desktopTrackIsActive(view.rows[0].track))
        assertFalse(desktopTrackIsActive(view.rows[1].track))
        assertEquals(null, view.rows[2].track)
        assertEquals(1_000, prepareDesktopHistoryView(DesktopHistoryInput(state.copy(loading = true), library)).rows.size)
        assertEquals(1_000, prepareDesktopHistoryView(DesktopHistoryInput(state.copy(errorCode = "read_failed"), library)).rows.size)
        assertEquals(1, prepareDesktopHistoryView(DesktopHistoryInput(state.copy(events = listOf(events[0], events[0])), library)).rows.size)
        assertTrue(prepareDesktopHistoryView(DesktopHistoryInput(state, null)).rows.all { it.track == null })
        val home = prepareDesktopHomeView(DesktopHomeInput(library.copy(history = events)))
        assertEquals(6, home.recent.size)
        assertEquals(events.take(6).map { it.id }, home.recent.map { it.event.id })
        assertTrue(home.hasActiveTracks)
        assertFalse(prepareDesktopHomeView(DesktopHomeInput(library.copy(tracks = listOf(removed)))).hasActiveTracks)
        assertFalse(prepareDesktopHomeView(DesktopHomeInput(DesktopLibrarySnapshot())).hasActiveTracks)
        assertTrue(prepareDesktopHomeView(DesktopHomeInput(null)).recent.isEmpty())
        assertFalse(DesktopHomeInput(library) == DesktopHomeInput(library.copy()))
        assertTrue(DesktopHomeInput(library) == DesktopHomeInput(library))
        var checkpoints = 0
        try {
            prepareDesktopHistoryView(input) {
                if (++checkpoints == 3) throw CancellationException("history_preparation_cancelled")
            }
            fail("History preparation cancellation must propagate")
        } catch (expected: CancellationException) {
            assertEquals("history_preparation_cancelled", expected.message)
        }
    }

    private fun assertTasteCommandTargets(): Unit {
        val state = DesktopTasteState("queue", "duplicate-entry", "listen-1",
            listenCommandsEnabled = true, sessionCommandsEnabled = true)
        val library = DesktopLibrarySnapshot(queue = DesktopQueue("queue",
            listOf(DesktopQueueEntry("duplicate-entry", "duplicate-track"), DesktopQueueEntry("other-duplicate", "duplicate-track")),
            "duplicate-entry", sessionId = "listen-1"))
        val input = DesktopTasteInput(state, library, DesktopSection.Library, 1, 1, false)
        val target = DesktopTasteTarget(input)
        assertEquals(input, DesktopTasteInput(state, library, DesktopSection.Library, 1, 1, false))
        assertFalse(input == DesktopTasteInput(state.copy(), library, DesktopSection.Library, 1, 1, false))
        assertFalse(input == DesktopTasteInput(state, library.copy(), DesktopSection.Library, 1, 1, false))
        assertTrue(target.canSetListen(target, false, true))
        assertTrue(target.canSetQueue(target, false, true))
        // Audio availability/track preferences are absent from this metadata admission policy.
        assertFalse(target.canSetListen(target, true, true))
        assertFalse(target.canSetQueue(target, true, true))
        assertFalse(target.canSetListen(target, false, false))
        assertFalse(target.canSetQueue(target, false, false))
        val retired = listOf(
            DesktopTasteInput(state.copy(queueId = "replacement"), library, DesktopSection.Library, 1, 1, false),
            DesktopTasteInput(state.copy(currentEntryId = "other-duplicate"), library, DesktopSection.Library, 1, 1, false),
            // Replay can retain entry/track identity but must retire the old listen target.
            DesktopTasteInput(state.copy(listeningEventId = "listen-2"), library, DesktopSection.Library, 1, 1, false),
            DesktopTasteInput(state.copy(sessionTasteState = "EXCLUDED"), library, DesktopSection.Library, 1, 1, false),
            DesktopTasteInput(state.copy(listenTasteState = "EXCLUDED"), library, DesktopSection.Library, 1, 1, false),
            DesktopTasteInput(state.copy(listenCommandsEnabled = false), library, DesktopSection.Library, 1, 1, false),
            DesktopTasteInput(state.copy(sessionCommandsEnabled = false), library, DesktopSection.Library, 1, 1, false),
            DesktopTasteInput(state.copy(), library, DesktopSection.Library, 1, 1, false),
            DesktopTasteInput(state, library.copy(), DesktopSection.Library, 1, 1, false),
            DesktopTasteInput(state, library, DesktopSection.Queue, 1, 1, false),
            // A/B/A navigation and details close/reopen have different scalar revisions.
            DesktopTasteInput(state, library, DesktopSection.Library, 3, 1, false),
            DesktopTasteInput(state, library, DesktopSection.Library, 1, 3, false),
            DesktopTasteInput(state, library, DesktopSection.Library, 1, 1, true),
        )
        var listenCalls = 0
        var queueCalls = 0
        val choices = mutableListOf<Boolean>()
        val callbacks = DesktopTasteCallbacks(onSetCurrentListenExcluded = { queue, entry, listen, excluded ->
            assertEquals("queue", queue)
            assertEquals("duplicate-entry", entry)
            assertEquals("listen-1", listen)
            choices += excluded
            listenCalls += 1
        }, onSetQueueSessionExcluded = { queue, excluded ->
            assertEquals("queue", queue)
            choices += excluded
            queueCalls += 1
        })
        retired.forEach { newer ->
            assertFalse(input == newer)
            val current = DesktopTasteTarget(newer)
            assertFalse(target.canSetListen(current, false, true))
            assertFalse(target.canSetQueue(current, false, true))
            target.setListen(current, false, true, true, callbacks)
            target.setQueue(current, false, true, true, callbacks)
        }
        // Even returning to the exact original references cannot resurrect an old rendering.
        val returned = DesktopTasteTarget(input)
        assertFalse(target.canSetListen(returned, false, true))
        assertFalse(target.canSetQueue(returned, false, true))
        target.setListen(returned, false, true, true, callbacks)
        target.setQueue(returned, false, true, true, callbacks)
        target.setListen(target, true, true, true, callbacks)
        target.setQueue(target, true, true, true, callbacks)
        target.setListen(target, false, false, true, callbacks)
        target.setQueue(target, false, false, true, callbacks)
        assertEquals(0, listenCalls)
        assertEquals(0, queueCalls)
        // Each click emits its explicit choice, not an inversion of the confirmed projection.
        listOf(true, true, false).forEach { excluded ->
            target.setListen(target, false, true, excluded, callbacks)
            target.setQueue(target, false, true, excluded, callbacks)
        }
        assertEquals(3, listenCalls)
        assertEquals(3, queueCalls)
        assertEquals(listOf(true, true, true, true, false, false), choices)
        assertEquals("INCLUDED", state.sessionTasteState)
        assertEquals("INCLUDED", state.listenTasteState)
        listOf(state.copy(queueId = null), state.copy(queueId = ""), state.copy(currentEntryId = null),
            state.copy(currentEntryId = " "), state.copy(listeningEventId = null), state.copy(listeningEventId = ""),
            state.copy(listenCommandsEnabled = false), DesktopTasteState()).forEach { disabled ->
            val current = DesktopTasteTarget(DesktopTasteInput(disabled, library, DesktopSection.Library, 1, 1, false))
            assertFalse(current.canSetListen(current, false, true))
        }
        listOf(state.copy(queueId = null), state.copy(queueId = " "), state.copy(sessionCommandsEnabled = false),
            DesktopTasteState()).forEach { disabled ->
            val current = DesktopTasteTarget(DesktopTasteInput(disabled, library, DesktopSection.Library, 1, 1, false))
            assertFalse(current.canSetQueue(current, false, true))
        }
        val queueOnly = state.copy(currentEntryId = null, listeningEventId = null, listenCommandsEnabled = false)
        val queueTarget = DesktopTasteTarget(DesktopTasteInput(queueOnly, library, DesktopSection.Queue, 1, 1, false))
        assertTrue(queueTarget.canSetQueue(queueTarget, false, true))
        assertFalse(queueTarget.canSetListen(queueTarget, false, true))
        queueTarget.setQueue(queueTarget, false, true, false, callbacks)
        assertEquals(4, queueCalls)
        val listenOnly = state.copy(sessionCommandsEnabled = false)
        val listenTarget = DesktopTasteTarget(DesktopTasteInput(listenOnly, library, DesktopSection.Library, 1, 1, false))
        assertTrue(listenTarget.canSetListen(listenTarget, false, true))
        assertFalse(listenTarget.canSetQueue(listenTarget, false, true))
        listenTarget.setListen(listenTarget, false, true, true, callbacks)
        assertEquals(4, listenCalls)
        val future = state.copy(sessionTasteState = "FUTURE", listenTasteState = "future-listen")
        val futureTarget = DesktopTasteTarget(DesktopTasteInput(future, library, DesktopSection.Library, 1, 1, false))
        assertTrue(futureTarget.canSetListen(futureTarget, false, true))
        assertTrue(futureTarget.canSetQueue(futureTarget, false, true))
        futureTarget.setListen(futureTarget, false, true, false, callbacks)
        futureTarget.setQueue(futureTarget, false, true, true, callbacks)
        assertEquals("FUTURE", future.sessionTasteState)
        assertEquals("future-listen", future.listenTasteState)
        val locked = DesktopTasteTarget(DesktopTasteInput(state, library, DesktopSection.Library, 1, 1, true))
        assertFalse(locked.canSetListen(locked, false, true))
        assertFalse(locked.canSetQueue(locked, false, true))
    }

    private fun assertTasteCapturedPolicy(): Unit {
        val included = DesktopTastePolicy.INCLUDED
        val excluded = DesktopTastePolicy.EXCLUDED
        assertEquals(false, desktopTasteChoice(included))
        assertEquals(true, desktopTasteChoice(excluded))
        assertEquals(DesktopTasteMark.INCLUDED, desktopTasteMark(included, included))
        listOf(included to excluded, excluded to included, excluded to excluded).forEach { (queue, listen) ->
            assertEquals(DesktopTasteMark.EXCLUDED, desktopTasteMark(queue, listen))
            assertEquals(true, DesktopTastePolicy.isExcluded(queue, listen))
            assertFalse(desktopTasteHasUnsupported(queue, listen))
        }
        assertEquals(false, DesktopTastePolicy.isExcluded(included, included))
        assertEquals(included, DesktopTastePolicy.forNewListen(included))
        assertEquals(included, DesktopTastePolicy.forNewListen(excluded))
        listOf("FUTURE", "excluded", "included", " EXCLUDED", "INCLUDED ", "", "\u0000\u202Eprivate", "x".repeat(4097)).forEach { future ->
            assertFalse(DesktopTastePolicy.isKnown(future))
            assertEquals(null, desktopTasteChoice(future))
            assertEquals(future, DesktopTastePolicy.forNewListen(future))
            assertEquals(null, DesktopTastePolicy.isExcluded(included, future))
            assertEquals(null, DesktopTastePolicy.isExcluded(future, included))
            assertEquals(DesktopTasteMark.UNSUPPORTED, desktopTasteMark(future, future))
            assertEquals(DesktopTasteMark.EXCLUDED, desktopTasteMark(future, excluded))
            assertEquals(DesktopTasteMark.EXCLUDED, desktopTasteMark(excluded, future))
            assertTrue(desktopTasteHasUnsupported(future, excluded))
            assertTrue(desktopTasteHasUnsupported(excluded, future))
            assertFalse(future == DesktopTastePolicy.choice(false))
            assertFalse(future == DesktopTastePolicy.choice(true))
        }
        val old = DesktopListeningEvent("old", "entry", "track", 1, 30_000, 30_000, true)
        assertEquals(included, old.sessionTasteState)
        assertEquals(included, old.listenTasteState)
        assertEquals(included, DesktopTasteState().sessionTasteState)
        assertEquals(included, DesktopTasteState().listenTasteState)
        val entries = listOf(DesktopQueueEntry("entry", "track"))
        val queue = DesktopQueue("queue", entries, "entry", sessionId = "logical-listen",
            sessionTasteState = excluded, listenTasteState = excluded)
        val nextListen = queue.copy(sessionId = "next-listen", listenTasteState = DesktopTastePolicy.forNewListen(queue.listenTasteState))
        assertEquals(excluded, nextListen.sessionTasteState)
        assertEquals(included, nextListen.listenTasteState)
        assertEquals(DesktopTasteMark.EXCLUDED, desktopTasteMark(nextListen.sessionTasteState, nextListen.listenTasteState))
        assertEquals(included, DesktopQueue("new-queue", entries, "entry").sessionTasteState)
        val track = DesktopTrack("track", "Title", "Artist", null, "a".repeat(64), 1,
            preference = "DISLIKED", collectionState = "REMOVED")
        val library = DesktopLibrarySnapshot(listOf(track), queue = queue, history = listOf(old))
        // Annotation must read the event, not the current excluded queue or disliked/removed track.
        DesktopLanguage.entries.forEach { language ->
            assertEquals("${DesktopText.TasteHistoryMark.inLanguage(language)}: ${DesktopText.TasteIncluded.inLanguage(language)}",
                desktopHistoryTasteAnnotation(library.history.single(), language))
            assertEquals(desktopHistoryTasteAnnotation(old, language), desktopHistoryTasteAnnotation(old.copy(finalized = false), language))
        }
        assertEquals(queue, library.queue)
        assertEquals(entries, library.queue?.entries)
        assertEquals("logical-listen", library.queue?.sessionId)
        assertEquals(old, library.history.single())
        assertEquals("DISLIKED", track.preference)
        assertEquals("REMOVED", track.collectionState)
        assertFalse(desktopTrackIsActive(track))
        assertTrue(DesktopTrackPreferenceFilter.DISLIKED.matches(track.preference))
    }

    private fun assertTasteCopyAndErrors(): Unit {
        assertTrue(DesktopText.TasteLocalOnly.russian.contains("сервер не получает"))
        assertTrue(DesktopText.TasteLocalOnly.english.contains("server does not receive"))
        assertTrue(DesktopText.TasteLocalOnly.russian.contains("пока не работают"))
        assertTrue(DesktopText.TasteLocalOnly.english.contains("do not yet run on Mac"))
        assertTrue(DesktopText.TasteListenScope.russian.contains("неподдерживаемая сохраняется"))
        assertTrue(DesktopText.TasteListenScope.english.contains("unsupported mark is preserved"))
        assertTrue(DesktopText.TasteQueueScope.russian.contains("после перезапуска"))
        assertTrue(DesktopText.TasteQueueScope.english.contains("after restart"))
        assertEquals("Вся текущая очередь", DesktopText.TasteCurrentQueue.russian)
        assertEquals("Entire current queue", DesktopText.TasteCurrentQueue.english)
        assertEquals("Current listen", DesktopText.TasteCurrentListen.english)
        assertEquals("Включить", DesktopText.TasteChooseInclude.russian)
        assertEquals("Exclude", DesktopText.TasteChooseExclude.english)
        assertEquals(null, desktopTasteErrorText(null, false))
        assertEquals(DesktopText.TasteListenStale, desktopTasteErrorText("TASTE_LISTEN_STALE", false))
        assertEquals(DesktopText.TasteQueueStale, desktopTasteErrorText("TASTE_SESSION_STALE", false))
        assertEquals(DesktopText.TasteUnavailable, desktopTasteErrorText("TASTE_EXCLUSION_UNAVAILABLE", false))
        listOf(null, "TASTE_LISTEN_STALE", "TASTE_SESSION_STALE", "TASTE_EXCLUSION_UNAVAILABLE", "private://unknown").forEach { code ->
            assertEquals(DesktopText.LibraryReopenRequired, desktopTasteErrorText(code, true))
        }
        listOf("desktop_snapshot_unavailable", "import_library_reopen_required").forEach { code ->
            assertTrue(desktopTasteRequiresReopen(code))
            assertEquals(DesktopText.LibraryReopenRequired, desktopTasteErrorText(code, false))
        }
        listOf(null, "AUDIO_ERROR", "TASTE_EXCLUSION_UNAVAILABLE", "private://unknown").forEach { code ->
            assertFalse(desktopTasteRequiresReopen(code))
        }
        DesktopLanguage.entries.forEach { language ->
            assertEquals(DesktopText.AudioError.inLanguage(language), desktopErrorMessage("AUDIO_ERROR", language))
            assertEquals(DesktopText.TasteSaveUnconfirmed, desktopTasteErrorText("AUDIO_ERROR", false))
            listOf("private://unknown", "\u0000\u202Eprivate", "x".repeat(4097)).forEach { code ->
                assertEquals(DesktopText.TasteSaveUnconfirmed, desktopTasteErrorText(code, false))
            }
            assertEquals(DesktopText.TasteIncluded.inLanguage(language), desktopTasteStatus("INCLUDED", "INCLUDED", language))
            assertEquals(DesktopText.TasteExcluded.inLanguage(language), desktopTasteStatus("INCLUDED", "EXCLUDED", language))
            listOf("FUTURE", "excluded", "EXCLUDED ", "\u0000\u202Eprivate", "x".repeat(4097)).forEach { future ->
                assertEquals(DesktopText.TasteUnsupported.inLanguage(language), desktopTasteStatus("INCLUDED", future, language))
                listOf(future to "EXCLUDED", "EXCLUDED" to future).forEach { (queue, listen) ->
                    val event = DesktopListeningEvent("event", "entry", "track", 1, 1, 1, true, queue, listen)
                    assertEquals("${DesktopText.TasteHistoryMark.inLanguage(language)}: ${DesktopText.TasteExcluded.inLanguage(language)}" +
                        " · ${DesktopText.TasteUnsupportedWarning.inLanguage(language)}", desktopHistoryTasteAnnotation(event, language))
                    assertEquals(desktopHistoryTasteAnnotation(event, language), desktopHistoryTasteAnnotation(event.copy(finalized = false), language))
                }
                val unsupported = DesktopListeningEvent("event", "entry", "track", 1, 1, 1, true, "INCLUDED", future)
                assertEquals("${DesktopText.TasteHistoryMark.inLanguage(language)}: ${DesktopText.TasteUnsupported.inLanguage(language)}" +
                    " · ${DesktopText.TasteUnsupportedWarning.inLanguage(language)}", desktopHistoryTasteAnnotation(unsupported, language))
            }
        }
    }

    private fun assertCollectionConfirmation(): Unit {
        val track = DesktopTrack("retained", "Title", "Artist", 1_000, "a".repeat(64), 1, preference = "LIKED")
        val snapshot = DesktopLibrarySnapshot(tracks = listOf(track))
        var revision = 1L
        var selected = track.id
        val input = DesktopTrackInput(snapshot, track.id, null, 1) { selected == track.id && revision == 1L }
        val view = DesktopTrackView(input, track)
        val target = DesktopTrackCollectionRemoval(input, track)
        assertTrue(target.canConfirm(input, view, false))
        assertFalse(target.canConfirm(input, view, true))
        assertFalse(target.canConfirm(input, DesktopTrackView(), false))
        assertFalse(target.canConfirm(input, DesktopTrackView(input), false))
        assertFalse(target.canConfirm(input, DesktopTrackView(input, track.copy(collectionState = "REMOVED")), false))
        assertFalse(target.canConfirm(input, DesktopTrackView(input, track.copy(id = "other")), false))
        val newer = DesktopTrackInput(snapshot.copy(), track.id, null, 1)
        assertFalse(target.canConfirm(newer, DesktopTrackView(newer, track), false))
        selected = "other"
        assertFalse(target.canConfirm(input, view, false))
        selected = track.id
        revision = 3
        val reopened = DesktopTrackInput(snapshot, track.id, null, 3)
        assertFalse(target.canConfirm(input, view, false))
        assertFalse(target.canConfirm(reopened, DesktopTrackView(reopened, track), false))
        assertFalse(desktopTrackCollectionCanRestore(reopened, DesktopTrackView(reopened, track), false))
        listOf("REMOVED", "FUTURE", "active", "\u0000\u202Eprivate").forEach { state ->
            val retained = track.copy(collectionState = state)
            val retainedSnapshot = snapshot.copy(tracks = listOf(retained))
            val retainedInput = DesktopTrackInput(retainedSnapshot, track.id, null)
            val retainedView = DesktopTrackView(retainedInput, retained)
            assertTrue(desktopTrackCollectionCanRestore(retainedInput, retainedView, false))
            assertFalse(desktopTrackCollectionCanRestore(retainedInput, retainedView, true))
            assertFalse(DesktopTrackCollectionRemoval(retainedInput, retained).canConfirm(retainedInput, retainedView, false))
            assertTrue(desktopTrackPreferenceCanWrite(retainedInput, retainedView, false))
            assertTrue(DesktopTrackTextDraft(retained.id, retained.title, retained.artist, "Edited").canSubmit(retained))
            assertEquals(state, retained.collectionState)
            assertEquals("LIKED", retained.preference)
        }
        val filterInput = DesktopLibraryInput(snapshot, "", DesktopLibrarySort.Title, DesktopSortDirection.Ascending,
            DesktopPlaylistInput(snapshot, null), collectionFilter = DesktopTrackCollectionFilter.REMOVED)
        assertTrue(filterInput.isCurrent(snapshot, "", DesktopLibrarySort.Title, DesktopSortDirection.Ascending,
            DesktopTrackPreferenceFilter.ALL, 0, null, 0, DesktopTrackCollectionFilter.REMOVED))
        assertFalse(filterInput.isCurrent(snapshot, "", DesktopLibrarySort.Title, DesktopSortDirection.Ascending,
            DesktopTrackPreferenceFilter.ALL, 0, null, 0, DesktopTrackCollectionFilter.ACTIVE))
        assertFalse(filterInput.isCurrent(snapshot, "", DesktopLibrarySort.Title, DesktopSortDirection.Ascending,
            DesktopTrackPreferenceFilter.ALL, 2, null, 0, DesktopTrackCollectionFilter.REMOVED))
        assertFalse(filterInput == DesktopLibraryInput(snapshot, "", DesktopLibrarySort.Title, DesktopSortDirection.Ascending,
            DesktopPlaylistInput(snapshot, null), collectionFilter = DesktopTrackCollectionFilter.ALL))
    }

    private fun assertCollectionSelections(): Unit {
        val active = DesktopTrack("a", "Z song", "Artist", null, "a".repeat(64), 1, preference = "LIKED")
        val removed = active.copy(id = "r", title = "A song", collectionState = "REMOVED")
        val unknown = active.copy(id = "u", title = "B song", collectionState = "FUTURE")
        val disliked = active.copy(id = "d", title = "C song", preference = "DISLIKED")
        val tracks = listOf(removed, unknown, active, disliked)
        assertEquals("ACTIVE", active.collectionState)
        assertTrue(desktopTrackIsActive(disliked))
        assertFalse(desktopTrackIsActive(null))
        listOf("FUTURE", "active", " ACTIVE", "REMOVED ", "").forEach { state ->
            assertTrue(DesktopTrackCollectionFilter.ALL.matches(state))
            assertFalse(DesktopTrackCollectionFilter.ACTIVE.matches(state))
            assertFalse(DesktopTrackCollectionFilter.REMOVED.matches(state))
            assertFalse(desktopTrackIsActive(active.copy(collectionState = state)))
        }
        fun matches(collection: DesktopTrackCollectionFilter = DesktopTrackCollectionFilter.ACTIVE,
            preference: DesktopTrackPreferenceFilter = DesktopTrackPreferenceFilter.ALL): List<DesktopTrack> =
            prepareDesktopLibraryMatches(tracks, "SONG", preference, DesktopLibrarySort.Title, DesktopSortDirection.Ascending, collection)
        assertEquals(listOf("d", "a"), matches().map { it.id })
        assertEquals(listOf("a"), matches(preference = DesktopTrackPreferenceFilter.LIKED).map { it.id })
        assertEquals(listOf("r"), matches(DesktopTrackCollectionFilter.REMOVED, DesktopTrackPreferenceFilter.LIKED).map { it.id })
        assertEquals(listOf("r", "u", "d", "a"), matches(DesktopTrackCollectionFilter.ALL).map { it.id })
        val selection = prepareDesktopLibraryCollectionSelection(matches(DesktopTrackCollectionFilter.ALL))
        assertEquals(listOf("d", "a"), selection.playIds)
        assertEquals(mapOf("d" to 0, "a" to 1), selection.positions)
        assertTrue(prepareDesktopLibraryCollectionSelection(matches(DesktopTrackCollectionFilter.REMOVED)).playIds.isEmpty())
        val beyondCap = List(10_001) { removed.copy(id = "removed-$it") } + listOf(active, disliked)
        assertEquals(listOf("a"), prepareDesktopLibraryMatches(beyondCap, "song", DesktopTrackPreferenceFilter.LIKED,
            DesktopLibrarySort.Title, DesktopSortDirection.Ascending).take(10_000).map { it.id })
        val playlist = DesktopPlaylist("p", "Retained", listOf(DesktopPlaylistEntry("1", active.id),
            DesktopPlaylistEntry("2", active.id), DesktopPlaylistEntry("3", disliked.id)))
        val byId = tracks.associateBy { it.id }
        val full = prepareDesktopPlaylistCollectionSelection(playlist, byId)
        assertTrue(full.allActive)
        assertEquals(listOf("a", "a", "d"), full.playIds)
        listOf(removed.id, unknown.id, "missing").forEach { refused ->
            val mixed = playlist.copy(entries = playlist.entries + DesktopPlaylistEntry("hidden", refused))
            val displayed = prepareDesktopPlaylistView(DesktopPlaylistInput(DesktopLibrarySnapshot(tracks, listOf(mixed)), mixed.id), 2)
            assertEquals(2, displayed.rows.size)
            val blocked = prepareDesktopPlaylistCollectionSelection(displayed.playlist, byId)
            assertFalse(blocked.allActive)
            assertTrue(blocked.playIds.isEmpty())
            val admission = desktopPlaylistBulkAdmission(mixed, 0, blocked.allActive, true, false, true, false)
            assertFalse(admission.enqueue)
            assertFalse(admission.playNext)
            assertEquals(refused, mixed.entries.last().trackId)
        }
        assertTrue(prepareDesktopPlaylistCollectionSelection(playlist.copy(entries = List(1_001) {
            DesktopPlaylistEntry("entry-$it", active.id)
        }), byId).playIds.isEmpty())
        assertFalse(prepareDesktopPlaylistCollectionSelection(playlist.copy(entries = emptyList()), byId).allActive)
        assertFalse(prepareDesktopPlaylistCollectionSelection(null, byId).allActive)
        var checkpoints = 0
        try {
            prepareDesktopPlaylistCollectionSelection(playlist, byId) {
                if (++checkpoints == 2) throw CancellationException("collection_cancelled")
            }
            fail("Full collection admission must cooperate with cancellation")
        } catch (expected: CancellationException) {
            assertEquals("collection_cancelled", expected.message)
        }
        assertEquals(listOf("REMOVED", "FUTURE", "ACTIVE", "ACTIVE"), tracks.map { it.collectionState })
    }

    private fun assertMultiSelectionPolicies(): Unit {
        val active = DesktopTrack("a", "A", "Artist", null, "a".repeat(64), 100)
        val tracks = List(1_001) { active.copy(id = "id-$it", title = "Song $it") }
        val byId = tracks.associateBy { it.id }
        var selection = DesktopMultiSelection()
        assertSame(selection, selection.setSelected("removed", true, false))
        selection = selection.setSelected("id-102", true, true)
        // Consecutive explicit checkbox values use the latest state and are idempotent.
        selection = selection.setSelected("id-0", true, true)
        assertSame(selection, selection.setSelected("id-102", true, true))
        val mutablePage = mutableListOf("id-100", "id-101", "id-100")
        selection = selection.selectPage(mutablePage)
        mutablePage.clear()
        assertEquals(listOf("id-102", "id-0", "id-100", "id-101"), selection.ids)
        assertSame(selection, selection.selectPage(listOf("id-101", "id-100")))
        val order = selection.ids.toList()
        val firstPage = desktopBrowseSlice(tracks, desktopBrowsePage(tracks.size, 0))
        val secondPage = desktopBrowseSlice(tracks, desktopBrowsePage(tracks.size, 1))
        listOf(firstPage, secondPage, firstPage.reversed()).forEach { shown ->
            val prepared = prepareDesktopMultiSelection(selection, byId, shown)
            assertSame(selection, prepared.selection)
            assertEquals(order, prepared.selection.ids)
            assertEquals(order.toSet(), prepared.selectedIds)
            assertTrue(prepared.allActive)
        }
        val filter = DesktopMultiSelectionFilter("", DesktopTrackCollectionFilter.ACTIVE,
            DesktopTrackPreferenceFilter.ALL, DesktopCopyAvailabilityFilter.ALL)
        listOf(filter.copy(query = "new"), filter.copy(collection = DesktopTrackCollectionFilter.ALL),
            filter.copy(preference = DesktopTrackPreferenceFilter.LIKED),
            filter.copy(copy = DesktopCopyAvailabilityFilter.PRESENT)).forEach { changed ->
            assertFalse(filter == changed)
            val cleared = selection.clear()
            assertTrue(cleared.ids.isEmpty())
            assertTrue(cleared.revision > selection.revision)
            assertNotSame(selection, cleared)
        }
        val missing = byId - "id-102"
        val invalidById = missing + mapOf("id-0" to tracks[0].copy(collectionState = "REMOVED"),
            "id-100" to tracks[100].copy(collectionState = "FUTURE_STATE"))
        val invalid = prepareDesktopMultiSelection(selection, invalidById, listOf(tracks[0].copy(collectionState = "REMOVED"), tracks[1]))
        assertEquals(listOf("id-102", "id-0", "id-100"), invalid.invalidIds)
        assertEquals(invalid.invalidIds, invalid.invalidRows.map { it.id })
        assertEquals(listOf("Unavailable track 1", "Song 0", "Song 100"), invalid.invalidRows.map { it.english })
        DesktopLanguage.entries.forEach { language ->
            assertEquals(DesktopText.MultiSelectionUnavailableTrack.inLanguage(language).replace("{position}", "1"),
                invalid.invalidRows.first().label(language))
            assertFalse(invalid.invalidRows.any { it.label(language).contains("id-") })
        }
        listOf("/private/path/song", "C:\\private\\song", "private://source", "", "Song\u0000", "Song\u202Etxt",
            "\uD800", "x".repeat(513)).forEach { unsafe ->
            val unsafeSelection = DesktopMultiSelection().setSelected("opaque-identity", true, true)
            val unsafeView = prepareDesktopMultiSelection(unsafeSelection,
                mapOf("opaque-identity" to active.copy(id = "opaque-identity", title = unsafe, collectionState = "REMOVED")), emptyList())
            DesktopLanguage.entries.forEach { language ->
                assertEquals(DesktopText.MultiSelectionUnavailableTrack.inLanguage(language).replace("{position}", "1"),
                    unsafeView.invalidRows.single().label(language))
            }
        }
        assertEquals(listOf("id-1"), invalid.activePageIds)
        assertEquals(order, invalid.selection.ids)
        assertFalse(invalid.allActive)
        assertSame(selection, selection.setSelected("id-0", true, false))
        val playlist = DesktopPlaylist("playlist", "Playlist", emptyList())
        val blocked = desktopMultiSelectionAdmission(invalid, 0, true, true, false, playlist, true, false)
        DesktopMultiSelectionCommand.entries.forEach { assertFalse(blocked.allows(it)) }
        var repaired = selection
        invalid.invalidIds.forEach { repaired = repaired.setSelected(it, false, false) }
        assertEquals(listOf("id-101"), repaired.ids)
        assertTrue(prepareDesktopMultiSelection(repaired, invalidById, emptyList()).allActive)
        val deselectedPage = selection.deselectPage(listOf("id-0", "id-100"))
        assertEquals(listOf("id-102", "id-101"), deselectedPage.ids)
        assertSame(deselectedPage, deselectedPage.setSelected("id-0", false, false))
        assertTrue(selection.clear().ids.isEmpty())
        var full = DesktopMultiSelection()
        repeat(10) { page -> full = full.selectPage(tracks.subList(page * 100, page * 100 + 100).map { it.id }) }
        assertEquals(1_000, full.ids.size)
        assertSame(full, full.setSelected("id-1000", true, true))
        assertSame(full, full.selectPage(listOf("id-999", "id-1000")))
        assertFalse(full.pageFits(listOf("id-999", "id-1000")))
        assertSame(full, full.selectPage(tracks.map { it.id }))
        val nearlyFull = full.deselectPage(tracks.takeLast(51).map { it.id })
        assertEquals(950, nearlyFull.ids.size)
        val wholePage = tracks.subList(901, 1_001).map { it.id }
        assertFalse(nearlyFull.pageFits(wholePage))
        assertSame(nearlyFull, nearlyFull.selectPage(wholePage))
        assertEquals(950, nearlyFull.ids.size)
        val frozen = full.retireIntent()
        assertNotSame(full, frozen)
        assertNotSame(full.ids, frozen.ids)
        assertEquals(full.ids, frozen.ids)
        assertTrue(frozen.revision > full.revision)
        var checkboxSelection = DesktopMultiSelection()
        var pageCurrent = true
        var limits = 0
        fun checkbox(id: String, activeRow: Boolean = true): (Boolean) -> Unit = desktopMultiSelectionCheckbox(id,
            activeRow, { pageCurrent }, { checkboxSelection }, { checkboxSelection = it }, { limits++ })
        val selectB = checkbox("b")
        val selectA = checkbox("a")
        selectB(true)
        selectA(true)
        val beforeRepeated = checkboxSelection
        selectB(true)
        assertSame(beforeRepeated, checkboxSelection)
        assertEquals(listOf("b", "a"), checkboxSelection.ids)
        pageCurrent = false
        selectB(false)
        assertSame(beforeRepeated, checkboxSelection)
        pageCurrent = true
        checkbox("removed", false)(true)
        assertSame(beforeRepeated, checkboxSelection)
        selectB(false)
        assertEquals(listOf("a"), checkboxSelection.ids)
        checkboxSelection = full
        checkbox("id-1000")(true)
        assertSame(full, checkboxSelection)
        assertEquals(1, limits)
        val prepared = prepareDesktopMultiSelection(selection, byId, firstPage)
        fun admission(queueSize: Int = 0, insertion: Boolean = true, controls: Boolean = true,
            current: Boolean = false, target: DesktopPlaylist? = playlist, fresh: Boolean = true,
            reopen: Boolean = false): DesktopMultiSelectionAdmission = desktopMultiSelectionAdmission(prepared,
                queueSize, insertion, controls, current, target, fresh, reopen)
        DesktopMultiSelectionCommand.entries.forEach { assertTrue(admission().allows(it)) }
        listOf(admission(fresh = false), admission(reopen = true)).forEach { gate ->
            DesktopMultiSelectionCommand.entries.forEach { assertFalse(gate.allows(it)) }
        }
        assertFalse(admission(queueSize = 997).enqueue)
        assertTrue(admission(queueSize = 996).enqueue)
        assertFalse(admission(queueSize = 1).playNext)
        assertTrue(admission(queueSize = 1, current = true).playNext)
        assertFalse(admission(controls = false).enqueue)
        assertFalse(admission(insertion = false).play)
        assertTrue(admission(insertion = false).append)
        assertFalse(admission(target = null).append)
        val empty = prepareDesktopMultiSelection(DesktopMultiSelection(), byId, firstPage)
        DesktopMultiSelectionCommand.entries.forEach {
            assertFalse(desktopMultiSelectionAdmission(empty, 0, true, true, false, playlist, true, false).allows(it))
        }
    }

    private fun assertMultiSelectionCommandTargets(): Unit {
        val selection = DesktopMultiSelection().selectPage(listOf("b", "a", "b"))
        val libraryScope = DesktopMultiSelectionScope(DesktopSection.Library, 7L)
        assertEquals(libraryScope, DesktopMultiSelectionScope(DesktopSection.Library, 7L))
        // Shell advances the revision synchronously, even if Library is the next composed route.
        val afterNavigationAba = DesktopMultiSelectionScope(DesktopSection.Library, 8L)
        assertFalse(libraryScope == afterNavigationAba)
        assertFalse(libraryScope == DesktopMultiSelectionScope(DesktopSection.Playlists, 7L))
        val playlist = DesktopPlaylist("playlist", "Playlist", emptyList())
        val library = DesktopLibrarySnapshot(playlists = listOf(playlist))
        fun input(snapshot: DesktopLibrarySnapshot = library, query: String = "", page: Int = 0,
            revision: Long = 1, playlistRevision: Long = 1): DesktopLibraryInput = DesktopLibraryInput(snapshot,
                query, DesktopLibrarySort.Title, DesktopSortDirection.Ascending,
                DesktopPlaylistInput(snapshot, playlist.id, playlistRevision), page = page, viewRevision = revision)
        val captured = input()
        val view = Any()
        fun target(): DesktopMultiSelectionActionTarget = DesktopMultiSelectionActionTarget(selection, captured, view, playlist)
        val base = target()
        // A changed remember scope creates empty selection, retiring old selected IDs and intent.
        val afterNavigationSelection = DesktopMultiSelection()
        assertTrue(afterNavigationSelection.ids.isEmpty())
        assertFalse(base.claim(afterNavigationSelection, captured, view, playlist, base, true, true))
        assertFalse(base.claim(selection.retireIntent(), captured, view, playlist, base, true, true))
        val selectionAba = selection.setSelected("a", false, false).setSelected("a", true, true)
        assertEquals(selection.ids, selectionAba.ids)
        assertFalse(base.claim(selectionAba, captured, view, playlist, base, true, true))
        assertFalse(base.claim(selection, input(library.copy()), view, playlist, base, true, true))
        assertFalse(base.claim(selection, input(query = "changed"), view, playlist, base, true, true))
        assertFalse(base.claim(selection, input(page = 1), view, playlist, base, true, true))
        assertFalse(base.claim(selection, input(revision = 3), view, playlist, base, true, true))
        assertFalse(base.claim(selection, input(playlistRevision = 3), view, playlist, base, true, true))
        assertFalse(base.claim(selection, captured, Any(), playlist, base, true, true))
        assertFalse(base.claim(selection, captured, view, playlist.copy(), base, true, true))
        assertFalse(base.claim(selection, captured, view, null, base, true, true))
        assertFalse(base.claim(selection, captured, view, playlist, target(), true, true))
        assertFalse(base.claim(selection, captured, view, playlist, base, false, true))
        assertFalse(base.claim(selection, captured, view, playlist, base, true, false))
        assertEquals(playlist.id, base.playlistId)
        assertTrue(base.claim(selection, captured, view, playlist, base, true, true))
        assertFalse(base.claim(selection, captured, view, playlist, base, true, true))
        val calls = mutableListOf<Pair<String, List<String>>>()
        var unrelated = 0
        val owner = DesktopLibraryCallbacks(onImport = { unrelated++ }, onEnqueue = { unrelated++ },
            onPlayNext = { unrelated++ }, onAddToPlaylist = { _, _ -> unrelated++ },
            onPlayTracks = { ids, type -> calls += type to ids },
            onEnqueueTracks = { calls += "enqueue" to it }, onPlayTracksNext = { calls += "next" to it },
            onAddTracksToPlaylist = { id, ids -> calls += id to ids })
        var currentSelection = selection
        DesktopMultiSelectionCommand.entries.forEach { command ->
            val intent = DesktopMultiSelectionActionTarget(currentSelection, captured, view, playlist)
            val frozenSelection = currentSelection
            val retained = desktopMultiSelectionCommand(intent, command, claim = {
                intent.claim(currentSelection, captured, view, playlist, intent, true, true)
            }, retire = { currentSelection = currentSelection.retireIntent() }, callbacks = owner, queueType = "SEARCH")
            retained()
            retained()
            assertEquals(frozenSelection.ids, currentSelection.ids)
            assertNotSame(frozenSelection, currentSelection)
        }
        assertEquals(listOf("SEARCH", "enqueue", "next", playlist.id), calls.map { it.first })
        assertTrue(calls.all { it.second == listOf("b", "a") })
        assertEquals(0, unrelated)
        val lifetime = DesktopBrowseLifetime()
        var latestOwner = owner
        val guard = desktopBrowseIdentityGuard(lifetime, owner, { latestOwner }) { true }
        val old = target()
        var latestTarget = old
        val stale = desktopMultiSelectionCommand(old, DesktopMultiSelectionCommand.ENQUEUE, claim = {
            old.claim(selection, captured, view, playlist, latestTarget, true, guard())
        }, retire = {}, callbacks = owner, queueType = "LIBRARY")
        latestOwner = owner.copy()
        latestTarget = target()
        stale()
        latestOwner = owner
        // Returning to an equal/previous owner never returns to the old action target.
        latestTarget = target()
        stale()
        lifetime.retire()
        latestTarget = old
        stale()
        assertEquals(4, calls.size)
    }

    private fun assertBrowsePageMath(): Unit {
        assertEquals(100, DESKTOP_BROWSE_PAGE_SIZE)
        listOf(0, -1, Int.MIN_VALUE).forEach { total ->
            listOf(-1, 0, Int.MAX_VALUE).forEach { requested ->
                assertEquals(DesktopBrowsePage(0, 1, 0, 0, 0), desktopBrowsePage(total, requested))
            }
        }
        assertEquals(DesktopBrowsePage(0, 1, 0, 100, 100), desktopBrowsePage(100, Int.MAX_VALUE))
        assertEquals(DesktopBrowsePage(1, 2, 100, 101, 101), desktopBrowsePage(101, Int.MAX_VALUE))
        assertEquals(DesktopBrowsePage(0, 2, 0, 100, 101), desktopBrowsePage(101, Int.MIN_VALUE))
        val maximum = desktopBrowsePage(Int.MAX_VALUE, Int.MAX_VALUE)
        assertEquals(21_474_837, maximum.pages)
        assertEquals(21_474_836, maximum.index)
        assertEquals(2_147_483_600, maximum.start)
        assertEquals(Int.MAX_VALUE, maximum.end)
        assertEquals(47, maximum.end - maximum.start)
        val tracks = List(10_005) { "track-$it" }
        val first = desktopBrowsePage(tracks.size, 0)
        val last = desktopBrowsePage(tracks.size, Int.MAX_VALUE)
        assertEquals(101, last.pages)
        assertEquals(100, last.index)
        assertEquals(tracks.takeLast(5), desktopBrowseSlice(tracks, last))
        assertEquals(tracks, (0 until last.pages).flatMap { desktopBrowseSlice(tracks, desktopBrowsePage(tracks.size, it)) })
        assertEquals(tracks.take(100), desktopBrowseSlice(tracks, first))
        assertEquals(listOf("one"), desktopBrowseSlice(listOf("one"), last))
        assertTrue(desktopBrowseSlice(emptyList<String>(), last).isEmpty())
        assertTrue(desktopBrowseSlice(tracks, DesktopBrowsePage(Int.MAX_VALUE, -1, Int.MIN_VALUE, Int.MAX_VALUE, -1)).size <= 100)
        val mutable = mutableListOf("first", "second")
        val published = desktopBrowseSlice(mutable, desktopBrowsePage(mutable.size, 0))
        mutable.clear()
        assertEquals(listOf("first", "second"), published)
    }

    private fun assertPagedPlaylistPreparation(): Unit {
        val entries = List(10_005) { DesktopPlaylistEntry("page-entry-$it", "duplicate") }
        val selected = DesktopPlaylist("selected", "Z selected", entries)
        val playlists = List(1_101) { DesktopPlaylist("choice-$it", "A $it", emptyList()) } + selected
        val originalEntries = entries.toList()
        val originalPlaylists = playlists.toList()
        val snapshot = DesktopLibrarySnapshot(playlists = playlists)
        val captured = DesktopPlaylistInput(snapshot, selected.id, 7, choicePage = 0, entryPage = 1, browseRevision = 9)
        val view = prepareDesktopPagedPlaylistView(captured)
        assertTrue(view.input === captured)
        assertEquals(100, view.choices.size)
        assertEquals(1_102, view.matchCount)
        assertEquals(12, view.choicePage.pages)
        assertEquals(selected.id, view.playlist?.id)
        assertFalse(view.choices.any { it.id == selected.id })
        assertEquals((100 until 200).toList(), view.rows.map { it.position })
        assertEquals((101..200).toList(), view.rows.map { it.occurrence })
        assertTrue(view.rows.first().canMoveUp)
        assertTrue(view.rows.last().canMoveDown)
        assertEquals(0, view.entryPositions["page-entry-100"])
        assertEquals(99, view.entryPositions["page-entry-199"])
        assertEquals(null, view.entryPositions["page-entry-99"])
        assertEquals(List(100) { "duplicate" }, view.playIds)
        val orderedChoices = playlists.sortedWith(compareBy<DesktopPlaylist> { it.name.lowercase() }.thenBy { it.id })
        val allChoices = (0 until view.choicePage.pages).flatMap { page ->
            val prepared = prepareDesktopPagedPlaylistView(DesktopPlaylistInput(snapshot, selected.id, choicePage = page))
            assertTrue(prepared.choices.size <= 100)
            assertEquals(selected.id, prepared.playlist?.id)
            prepared.choices.map { it.id }
        }
        assertEquals(orderedChoices.map { it.id }, allChoices)
        val everyEntry = (0 until view.entryPage.pages).flatMap { page ->
            val prepared = prepareDesktopPagedPlaylistView(DesktopPlaylistInput(snapshot, selected.id, entryPage = page))
            assertTrue(prepared.rows.size <= 100)
            prepared.rows.map { row ->
                assertEquals(row.position + 1, row.occurrence)
                row.entry.id
            }
        }
        assertEquals(entries.map { it.id }, everyEntry)
        val first = prepareDesktopPagedPlaylistView(DesktopPlaylistInput(snapshot, selected.id))
        assertFalse(first.rows.first().canMoveUp)
        assertTrue(first.rows.last().canMoveDown)
        val last = prepareDesktopPagedPlaylistView(DesktopPlaylistInput(snapshot, selected.id,
            choicePage = Int.MAX_VALUE, entryPage = Int.MAX_VALUE))
        assertEquals(listOf("choice-999", selected.id), last.choices.map { it.id })
        assertEquals(100, last.entryPage.index)
        assertEquals(entries.takeLast(5), last.rows.map { it.entry })
        assertEquals(10_005, last.rows.last().occurrence)
        assertTrue(last.rows.first().canMoveUp)
        assertFalse(last.rows.last().canMoveDown)
        val moved = entries.toMutableList().apply {
            val boundary = this[100]
            this[100] = this[99]
            this[99] = boundary
        }
        val movedSnapshot = snapshot.copy(playlists = listOf(selected.copy(entries = moved)))
        val movedPage = prepareDesktopPagedPlaylistView(DesktopPlaylistInput(movedSnapshot, selected.id, entryPage = 1))
        assertEquals("page-entry-99", movedPage.rows.first().entry.id)
        assertEquals(100, movedPage.rows.first().position)
        val removedSnapshot = snapshot.copy(playlists = listOf(selected.copy(entries = entries.filterIndexed { index, _ -> index != 100 })))
        assertEquals("page-entry-101", prepareDesktopPagedPlaylistView(DesktopPlaylistInput(removedSnapshot,
            selected.id, entryPage = 1)).rows.first().entry.id)
        val shrunk = snapshot.copy(playlists = listOf(selected.copy(entries = entries.take(10_000))))
        val clamped = prepareDesktopPagedPlaylistView(DesktopPlaylistInput(shrunk, selected.id,
            choicePage = 11, entryPage = 100))
        assertEquals(0, clamped.choicePage.index)
        assertEquals(99, clamped.entryPage.index)
        assertEquals(9_999, clamped.rows.last().position)
        assertFalse(clamped.rows.last().canMoveDown)
        val filtered = prepareDesktopPagedPlaylistView(DesktopPlaylistInput(snapshot, selected.id, query = "A 10",
            choicePage = Int.MAX_VALUE, entryPage = 1))
        assertTrue(filtered.choices.all { it.name.contains("A 10") })
        assertEquals(playlists.count { it.name.contains("A 10") }, filtered.matchCount)
        assertEquals(selected.id, filtered.playlist?.id)
        assertEquals(1, filtered.entryPage.index)
        val noMatches = prepareDesktopPagedPlaylistView(DesktopPlaylistInput(snapshot, selected.id, query = "absent"))
        assertTrue(noMatches.choices.isEmpty())
        assertEquals(selected.id, noMatches.playlist?.id)
        val fallback = prepareDesktopPagedPlaylistView(DesktopPlaylistInput(snapshot, null, choicePage = 11))
        assertEquals(orderedChoices.first().id, fallback.playlist?.id)
        val empty = prepareDesktopPagedPlaylistView(DesktopPlaylistInput(DesktopLibrarySnapshot(), null,
            choicePage = Int.MAX_VALUE, entryPage = Int.MAX_VALUE))
        assertTrue(empty.rows.isEmpty())
        assertTrue(empty.choices.isEmpty())
        assertEquals(desktopBrowsePage(0, 0), empty.entryPage)
        var checkpoints = 0
        try {
            prepareDesktopPagedPlaylistView(DesktopPlaylistInput(DesktopLibrarySnapshot(playlists = listOf(selected)),
                selected.id, entryPage = 100)) {
                if (++checkpoints == 4) throw CancellationException("paged_playlist_cancelled")
            }
            fail("Preceding duplicate counting must cooperate with cancellation")
        } catch (expected: CancellationException) { assertEquals("paged_playlist_cancelled", expected.message) }
        assertEquals(originalEntries, selected.entries)
        assertEquals(originalPlaylists, snapshot.playlists)
        assertEquals(1, captured.entryPage)
        assertEquals(9L, captured.browseRevision)
    }

    private fun assertPagedCollectionSelections(): Unit {
        val active = DesktopTrack("active", "Active", "Artist", null, "a".repeat(64), 1)
        val removed = active.copy(id = "removed", collectionState = "REMOVED")
        val future = active.copy(id = "future", collectionState = "FUTURE")
        val tracks = List(1_000) { active.copy(id = "active-$it") }
        val retained = List(10_005) { removed.copy(id = "retained-$it") }
        val full = prepareDesktopLibraryResultSelection(retained + future + tracks)
        assertFalse(full.overflow)
        assertEquals(tracks.map { it.id }, full.playIds)
        assertEquals(999, full.positions["active-999"])
        val overflow = prepareDesktopLibraryResultSelection(tracks + active)
        assertTrue(overflow.overflow)
        assertTrue(overflow.playIds.isEmpty())
        assertTrue(overflow.positions.isEmpty())
        assertTrue(prepareDesktopLibraryResultSelection(emptyList()).playIds.isEmpty())
        assertFalse(prepareDesktopLibraryResultSelection(listOf(removed, future)).overflow)
        val pageTracks = listOf(removed, tracks[0], future, tracks[1], tracks[2])
        val page = prepareDesktopLibraryCollectionSelection(pageTracks)
        assertEquals(tracks.take(3).map { it.id }, page.playIds)
        assertEquals(listOf("active-1", "active-2"), page.playIds.drop(requireNotNull(page.positions["active-1"])))
        assertEquals(pageTracks.map { it.id }, prepareDesktopCopyInspectionIds(pageTracks))
        val byId = (tracks + listOf(active, removed, future)).associateBy { it.id }
        val playlist = DesktopPlaylist("paged", "Paged", List(10_005) { DesktopPlaylistEntry("entry-$it", active.id) })
        val snapshot = DesktopLibrarySnapshot(playlists = listOf(playlist))
        val view = prepareDesktopPagedPlaylistView(DesktopPlaylistInput(snapshot, playlist.id, entryPage = 1))
        val whole = prepareDesktopPlaylistCollectionSelection(playlist, byId)
        assertTrue(whole.allActive)
        assertTrue(whole.playIds.isEmpty())
        val pageSelection = prepareDesktopPlaylistPageCollectionSelection(view.playIds, byId)
        assertTrue(pageSelection.allActive)
        assertEquals(List(100) { active.id }, pageSelection.playIds)
        val suffix = prepareDesktopPlaylistPageCollectionSelection(view.playIds, byId,
            requireNotNull(view.entryPositions["entry-199"]))
        assertEquals(listOf(active.id), suffix.playIds)
        listOf(removed.id, future.id, "missing").forEach { refused ->
            val entries = playlist.entries.toMutableList().apply { this[150] = DesktopPlaylistEntry("entry-150", refused) }
            val changed = snapshot.copy(playlists = listOf(playlist.copy(entries = entries)))
            val changedView = prepareDesktopPagedPlaylistView(DesktopPlaylistInput(changed, playlist.id, entryPage = 1))
            assertEquals(100, changedView.rows.size)
            assertEquals(refused, changedView.rows[50].entry.trackId)
            assertTrue(changedView.rows[50].canMoveUp)
            assertTrue(changedView.rows[50].canMoveDown)
            assertFalse(prepareDesktopPlaylistPageCollectionSelection(changedView.playIds, byId).allActive)
            assertTrue(prepareDesktopPlaylistPageCollectionSelection(changedView.playIds, byId, 50).playIds.isEmpty())
            assertEquals(List(49) { active.id }, prepareDesktopPlaylistPageCollectionSelection(changedView.playIds, byId, 51).playIds)
            assertFalse(prepareDesktopPlaylistCollectionSelection(changedView.playlist, byId).allActive)
            assertEquals(List(100) { active.id }, prepareDesktopPlaylistPageCollectionSelection(view.playIds, byId).playIds)
        }
        listOf(-1, 100, Int.MAX_VALUE).forEach { index ->
            assertFalse(prepareDesktopPlaylistPageCollectionSelection(view.playIds, byId, index).allActive)
        }
        assertFalse(prepareDesktopPlaylistPageCollectionSelection(emptyList(), byId).allActive)
        assertFalse(prepareDesktopPlaylistPageCollectionSelection(List(101) { active.id }, byId).allActive)
        val mutableIds = mutableListOf(active.id, active.id)
        val publishedSelection = prepareDesktopPlaylistPageCollectionSelection(mutableIds, byId)
        mutableIds.clear()
        assertEquals(listOf(active.id, active.id), publishedSelection.playIds)
        assertEquals(List(1_000) { active.id }, prepareDesktopPlaylistCollectionSelection(
            playlist.copy(entries = playlist.entries.take(1_000)), byId).playIds)
        assertTrue(prepareDesktopPlaylistCollectionSelection(playlist.copy(entries = playlist.entries.take(1_001)), byId).playIds.isEmpty())
        var checkpoints = 0
        try {
            prepareDesktopLibraryResultSelection(tracks) {
                if (++checkpoints == 3) throw CancellationException("result_selection_cancelled")
            }
            fail("Bounded whole-result preparation must propagate cancellation")
        } catch (expected: CancellationException) { assertEquals("result_selection_cancelled", expected.message) }
        checkpoints = 0
        try {
            prepareDesktopPlaylistPageCollectionSelection(view.playIds, byId) {
                if (++checkpoints == 2) throw CancellationException("page_selection_cancelled")
            }
            fail("Exact page preparation must propagate cancellation")
        } catch (expected: CancellationException) { assertEquals("page_selection_cancelled", expected.message) }
        assertEquals("REMOVED", removed.collectionState)
        assertEquals("FUTURE", future.collectionState)
        assertEquals(List(100) { active.id }, view.playIds)
    }

    private fun assertLibraryOrdering(): Unit {
        val tracks = listOf(
            DesktopTrack("b", "Song", "beta", 2_000, "b".repeat(64), 1),
            DesktopTrack("a", "song", " ", null, "a".repeat(64), 1),
            DesktopTrack("c", "\u0410\u043b\u044c\u0431\u043e\u043c", "Alpha", -1, "c".repeat(64), 1),
            DesktopTrack("d", "Other", "alpha", 1_000, "d".repeat(64), 1),
            DesktopTrack("e", "Zero", "Alpha", 0, "e".repeat(64), 1),
        )
        val expectedOrders = listOf(
            Triple(DesktopLibrarySort.Title, DesktopSortDirection.Ascending, listOf("d", "a", "b", "e", "c")),
            Triple(DesktopLibrarySort.Title, DesktopSortDirection.Descending, listOf("c", "e", "a", "b", "d")),
            Triple(DesktopLibrarySort.Artist, DesktopSortDirection.Ascending, listOf("c", "d", "e", "b", "a")),
            Triple(DesktopLibrarySort.Artist, DesktopSortDirection.Descending, listOf("b", "c", "d", "e", "a")),
            Triple(DesktopLibrarySort.Duration, DesktopSortDirection.Ascending, listOf("e", "d", "b", "a", "c")),
            Triple(DesktopLibrarySort.Duration, DesktopSortDirection.Descending, listOf("b", "d", "e", "a", "c")),
        )
        expectedOrders.forEach { (sort, direction, expected) ->
            assertEquals(expected, sortDesktopLibraryTracks(tracks, sort, direction).map { it.id })
        }
        val beyondCap = List(10_005) { index ->
            tracks.first().copy(id = "item-$index", title = if (index == 10_000) "A" else "Z")
        }
        val ordered = sortDesktopLibraryTracks(beyondCap, DesktopLibrarySort.Title, DesktopSortDirection.Ascending)
        val displayed = ordered.take(10_000)
        assertEquals("item-10000", displayed.first().id)
        assertEquals(10_000, displayed.size)
        val lastPage = desktopBrowsePage(ordered.size, Int.MAX_VALUE)
        assertEquals(101, lastPage.pages)
        assertEquals(ordered.takeLast(5), desktopBrowseSlice(ordered, lastPage))
        assertEquals(ordered, (0 until lastPage.pages).flatMap { desktopBrowseSlice(ordered, desktopBrowsePage(ordered.size, it)) })
        assertEquals(10_005, ordered.map { it.id }.distinct().size)
        var checkpoints = 0
        try {
            sortDesktopLibraryTracks(tracks, DesktopLibrarySort.Title, DesktopSortDirection.Ascending) {
                if (++checkpoints == 3) throw CancellationException("sort_cancelled")
            }
            fail("Cancellation must propagate from preparation")
        } catch (expected: CancellationException) {
            assertEquals("sort_cancelled", expected.message)
        }
        assertEquals(listOf("b", "a", "c", "d", "e"), tracks.map { it.id })
        assertEquals(" ", tracks[1].artist)
        assertEquals(-1L, tracks[2].durationMs)
        assertPreferenceFiltering(tracks)
    }

    private fun assertPreferenceFiltering(tracks: List<DesktopTrack>): Unit {
        val preferences = listOf(tracks[0].copy(preference = "LIKED"), tracks[1].copy(preference = "LIKED"),
            tracks[2].copy(preference = "DISLIKED"), tracks[3], tracks[4].copy(preference = "FUTURE"))
        assertEquals("NEUTRAL", tracks[3].preference)
        DesktopTrackPreference.entries.forEach { preference ->
            assertEquals(preference, desktopTrackPreference(preference.storedValue))
        }
        listOf("FUTURE", "liked", "neutral", " NEUTRAL", "NEUTRAL ", "", "\u0000\u202Eprivate").forEach { unknown ->
            assertEquals(null, desktopTrackPreference(unknown))
            assertTrue(DesktopTrackPreferenceFilter.ALL.matches(unknown))
            assertFalse(DesktopTrackPreferenceFilter.LIKED.matches(unknown))
            assertFalse(DesktopTrackPreferenceFilter.DISLIKED.matches(unknown))
            assertFalse(DesktopTrackPreferenceFilter.NEUTRAL.matches(unknown))
        }
        val liked = prepareDesktopLibraryMatches(preferences, "SONG", DesktopTrackPreferenceFilter.LIKED,
            DesktopLibrarySort.Title, DesktopSortDirection.Ascending)
        val playIds = liked.map { it.id }
        assertEquals(listOf("a", "b"), playIds)
        assertEquals(listOf("b"), playIds.subList(1, playIds.size))
        assertEquals(listOf("a", "b"), prepareDesktopLibraryMatches(preferences, "song", DesktopTrackPreferenceFilter.LIKED,
            DesktopLibrarySort.Title, DesktopSortDirection.Descending).map { it.id })
        assertEquals(listOf("c"), prepareDesktopLibraryMatches(preferences, "альбом", DesktopTrackPreferenceFilter.DISLIKED,
            DesktopLibrarySort.Title, DesktopSortDirection.Ascending).map { it.id })
        assertEquals(listOf("d"), prepareDesktopLibraryMatches(preferences, "ALPHA", DesktopTrackPreferenceFilter.NEUTRAL,
            DesktopLibrarySort.Title, DesktopSortDirection.Ascending).map { it.id })
        assertEquals(listOf("d", "a", "b", "e", "c"), prepareDesktopLibraryMatches(preferences, "", DesktopTrackPreferenceFilter.ALL,
            DesktopLibrarySort.Title, DesktopSortDirection.Ascending).map { it.id })
        val beyondCap = List(10_001) { index ->
            tracks.first().copy(id = "filtered-$index", title = "A", preference = "DISLIKED")
        } + listOf(tracks.first().copy(id = "liked-b", title = "Z", preference = "LIKED"),
            tracks.first().copy(id = "liked-a", title = "z", preference = "LIKED"))
        assertEquals(listOf("liked-a", "liked-b"), prepareDesktopLibraryMatches(beyondCap, "z", DesktopTrackPreferenceFilter.LIKED,
            DesktopLibrarySort.Title, DesktopSortDirection.Ascending).take(10_000).map { it.id })
        var checkpoints = 0
        try {
            prepareDesktopLibraryMatches(beyondCap, "", DesktopTrackPreferenceFilter.LIKED,
                DesktopLibrarySort.Title, DesktopSortDirection.Ascending) {
                if (++checkpoints == 3) throw CancellationException("preference_filter_cancelled")
            }
            fail("Preference filtering cancellation must propagate")
        } catch (expected: CancellationException) {
            assertEquals("preference_filter_cancelled", expected.message)
        }
        assertEquals(listOf("LIKED", "LIKED", "DISLIKED", "NEUTRAL", "FUTURE"), preferences.map { it.preference })
        assertEquals("NEUTRAL", tracks.first().preference)
    }

    private fun assertUpcomingPolicies(): Unit {
        val entries = listOf("past", "now", "a", "b", "c", "d", "e").map { DesktopQueueEntry(it, "same-track") }
        val original = entries.toList()
        fun order(ids: List<String>, action: DesktopQueueGroupAction): List<String>? =
            planDesktopUpcomingEdit(entries, "now", ids, action)?.activeEntryIds
        assertEquals(listOf("past", "now", "b", "c", "a", "e", "d"),
            order(listOf("e", "c", "b"), DesktopQueueGroupAction.MOVE_UP))
        assertEquals(listOf("past", "now", "c", "a", "b", "e", "d"),
            order(listOf("d", "b", "a"), DesktopQueueGroupAction.MOVE_DOWN))
        assertEquals(listOf("past", "now", "b", "e", "a", "c", "d"),
            order(listOf("e", "b"), DesktopQueueGroupAction.MOVE_NEXT))
        assertEquals(listOf("past", "now", "a", "c", "d"),
            order(listOf("e", "b"), DesktopQueueGroupAction.REMOVE))
        assertEquals(setOf("e", "b"), planDesktopUpcomingEdit(entries, "now", listOf("e", "b"),
            DesktopQueueGroupAction.REMOVE)?.removedEntryIds)
        assertEquals(listOf("past", "now", "a", "c", "b", "d", "e"),
            order(listOf("a", "c"), DesktopQueueGroupAction.MOVE_UP))
        assertEquals(listOf("past", "now", "a", "c", "b", "d", "e"),
            order(listOf("b", "e"), DesktopQueueGroupAction.MOVE_DOWN))
        assertEquals(null, order(listOf("a", "b"), DesktopQueueGroupAction.MOVE_UP))
        assertEquals(null, order(listOf("d", "e"), DesktopQueueGroupAction.MOVE_DOWN))
        assertEquals(null, order(listOf("b", "a"), DesktopQueueGroupAction.MOVE_NEXT))
        val invalid = listOf(emptyList(), listOf("missing"), listOf("past"), listOf("now"),
            listOf("b", "b"), listOf("b", "missing"), listOf("b", "now"), listOf("b", "past"), List(1_001) { "b" })
        DesktopQueueGroupAction.entries.forEach { action ->
            invalid.forEach { assertEquals(null, planDesktopUpcomingEdit(entries, "now", it, action)) }
            assertEquals(null, planDesktopUpcomingEdit(entries + entries.last(), "now", listOf("b"), action))
            assertEquals(null, planDesktopUpcomingEdit(entries, "absent-current", listOf("b"), action))
            assertEquals(null, planDesktopUpcomingEdit(entries, null, listOf("b"), action))
            assertEquals(null, planDesktopUpcomingEdit(emptyList(), "now", listOf("b"), action))
            assertEquals(null, planDesktopUpcomingEdit(List(1_001) { DesktopQueueEntry("entry-$it", "track") },
                "entry-0", listOf("entry-1"), action))
        }
        assertEquals(original, entries)
        val mutableEntries = entries.toMutableList()
        val selected = mutableListOf("e", "b")
        val plan = requireNotNull(planDesktopUpcomingEdit(mutableEntries, "now", selected, DesktopQueueGroupAction.REMOVE))
        assertEquals(original, mutableEntries)
        assertEquals(listOf("e", "b"), selected)
        mutableEntries.clear()
        selected.clear()
        assertEquals(listOf("past", "now", "a", "c", "d"), plan.activeEntryIds)
        assertEquals(setOf("b", "e"), plan.removedEntryIds)
        val bounded = List(1_000) { DesktopQueueEntry("entry-$it", "track") }
        assertEquals(listOf("entry-0"), planDesktopUpcomingEdit(bounded, "entry-0",
            bounded.drop(1).map { it.id }, DesktopQueueGroupAction.REMOVE)?.activeEntryIds)

        val target = DesktopQueueEditTarget("queue", "now", "session", 4, 9)
        val editor = DesktopQueueEditorState(target, entries, "now", false)
        var selection = DesktopUpcomingSelection()
        val upcoming = entries.drop(2).map { it.id }
        val upcomingSet = upcoming.toSet()
        assertSame(selection, selection.setSelected("now", true, upcomingSet))
        assertSame(selection, selection.setSelected("past", true, upcomingSet))
        assertSame(selection, selection.setSelected("missing", true, upcomingSet))
        selection = selection.setSelected("e", true, upcomingSet).setSelected("b", true, upcomingSet)
        assertEquals(listOf("e", "b"), selection.ids)
        assertSame(selection, selection.setSelected("e", true, upcomingSet))
        val view = prepareDesktopUpcomingView(DesktopUpcomingInput(editor, selection))
        assertEquals(entries, view.rows.map { it.entry })
        assertEquals(upcoming, view.upcomingIds)
        assertEquals(upcomingSet, view.upcomingSet)
        assertEquals(setOf("e", "b"), view.selectedIds)
        assertEquals(listOf(false, false, true, true, true, true, true), view.rows.map { it.upcoming })
        assertFalse(view.rows[2].canMoveUp)
        assertFalse(view.rows.last().canMoveDown)
        DesktopQueueGroupAction.entries.forEach { action ->
            assertEquals(planDesktopUpcomingEdit(entries, "now", selection.ids, action) != null, action in view.allowed)
        }
        val mutableAll = upcoming.toMutableList()
        val all = selection.selectAll(mutableAll)
        mutableAll.clear()
        assertEquals(upcoming, all.ids)
        assertTrue(all.clear().ids.isEmpty())
        assertSame(all, all.selectAll(List(1_001) { "entry-$it" }))
        assertSame(all, all.selectAll(listOf("a", "a")))
        val full = selection.selectAll(List(1_000) { "entry-$it" })
        assertSame(full, full.setSelected("overflow", true, setOf("overflow")))
        assertEquals(listOf("b"), selection.setSelected("e", false, emptySet()).ids)
        val retired = selection.retireIntent()
        assertNotSame(selection, retired)
        assertNotSame(selection.ids, retired.ids)
        assertEquals(selection.ids, retired.ids)
        val invalidSelection = selection.selectAll(listOf("b", "missing"))
        assertTrue(prepareDesktopUpcomingView(DesktopUpcomingInput(editor, invalidSelection)).allowed.isEmpty())
        assertTrue(prepareDesktopUpcomingView(DesktopUpcomingInput(editor.copy(target = null), selection)).rows.isEmpty())
        assertTrue(prepareDesktopUpcomingView(DesktopUpcomingInput(editor.copy(currentEntryId = "a"), selection)).rows.isEmpty())
        assertTrue(prepareDesktopUpcomingView(DesktopUpcomingInput(editor.copy(entries = entries + entries.last()), selection)).rows.isEmpty())
        assertTrue(prepareDesktopUpcomingView(DesktopUpcomingInput(editor.copy(entries = List(1_001) {
            DesktopQueueEntry("entry-$it", "track")
        }), selection)).rows.isEmpty())
        val queue = DesktopQueue("queue", entries, "now", sessionId = "session")
        assertTrue(desktopUpcomingLegacyAligned(editor, queue))
        assertTrue(desktopUpcomingLegacyAligned(editor, queue.copy(positionMs = 123, playedMs = 99,
            sessionTasteState = "EXCLUDED")))
        listOf(queue.copy(id = "other"), queue.copy(currentEntryId = "a"), queue.copy(sessionId = "other"),
            queue.copy(entries = entries.reversed()), queue.copy(entries = entries.dropLast(1)),
            queue.copy(entries = entries.map { it.copy(trackId = "other") }), queue.copy(repeatMode = "FUTURE"),
            queue.copy(shuffleEnabled = true), queue.copy(entries = entries + entries.last())).forEach {
            assertFalse(desktopUpcomingLegacyAligned(editor, it))
        }
        val shuffled = queue.copy(entries = entries.reversed(), shuffleEnabled = true, shuffleOrder = entries.map { it.id })
        assertTrue(desktopUpcomingLegacyAligned(editor.copy(shuffleEnabled = true), shuffled))
        assertFalse(desktopUpcomingLegacyAligned(editor.copy(shuffleEnabled = true), shuffled.copy(shuffleOrder = listOf("missing"))))
        assertFalse(desktopUpcomingLegacyAligned(editor.copy(shuffleEnabled = true), shuffled.copy(shuffleOrder = List(entries.size) { "a" })))
        assertFalse(desktopUpcomingLegacyAligned(editor, null))
        try {
            prepareDesktopUpcomingView(DesktopUpcomingInput(editor, selection)) { throw CancellationException("queue_preparation_cancelled") }
            fail("Queue preparation cancellation must propagate")
        } catch (expected: CancellationException) {
            assertEquals("queue_preparation_cancelled", expected.message)
        }
    }

    private fun assertUpcomingCommandTargets(): Unit {
        val entries = listOf("now", "a", "b", "c", "d").map { DesktopQueueEntry(it, "same-track") }
        val editorTarget = DesktopQueueEditTarget("queue", "now", "session", 4, 9)
        val editor = DesktopQueueEditorState(editorTarget, entries, "now", true)
        val ids = mutableListOf("d", "b")
        val selection = DesktopUpcomingSelection().selectAll(ids)
        ids.clear()
        val input = DesktopUpcomingInput(editor, selection)
        val view = prepareDesktopUpcomingView(input)
        fun target(): DesktopUpcomingActionTarget = DesktopUpcomingActionTarget(input, view)
        val base = target()
        val action = DesktopQueueGroupAction.MOVE_NEXT
        assertFalse(base.claim(DesktopUpcomingInput(editor, selection.retireIntent()), view, base, action, true))
        assertFalse(base.claim(DesktopUpcomingInput(editor.copy(target = editorTarget.copy()), selection), view, base, action, true))
        assertEquals(input, DesktopUpcomingInput(editor.copy(entries = entries.toList()), selection))
        assertFalse(base.claim(input, prepareDesktopUpcomingView(input), base, action, true))
        assertFalse(base.claim(input, view, target(), action, true))
        assertFalse(base.claim(input, view, base, action, false))
        assertTrue(base.claim(input, view, base, action, true))
        assertFalse(base.claim(input, view, base, action, true))
        val firstSelection = DesktopUpcomingSelection().selectAll(listOf("a"))
        val firstInput = DesktopUpcomingInput(editor, firstSelection)
        val firstView = prepareDesktopUpcomingView(firstInput)
        val firstTarget = DesktopUpcomingActionTarget(firstInput, firstView)
        assertFalse(firstTarget.claim(firstInput, firstView, firstTarget, DesktopQueueGroupAction.MOVE_UP, true))
        assertFalse(firstTarget.claim(firstInput, firstView, firstTarget, DesktopQueueGroupAction.MOVE_NEXT, true))
        assertTrue(firstTarget.claim(firstInput, firstView, firstTarget, DesktopQueueGroupAction.REMOVE, true))

        val calls = mutableListOf<Triple<DesktopQueueEditTarget, DesktopQueueGroupAction, List<String>>>()
        var unrelated = 0
        val owner = DesktopLibraryCallbacks(onRemoveEntry = { unrelated++ }, onPlayTracks = { _, _ -> unrelated++ },
            onEditUpcoming = { captured, command, selected -> calls += Triple(captured, command, selected) })
        val scope = DesktopUpcomingScope(editorTarget, 7)
        // Rebuilt callback/editor DTOs, position and metadata publication preserve the selection scope.
        assertEquals(scope, DesktopUpcomingScope(editorTarget, 7))
        assertFalse(DesktopUpcomingOwnerKey(owner) == DesktopUpcomingOwnerKey(owner.copy()))
        assertEquals(DesktopUpcomingOwnerKey(owner), DesktopUpcomingOwnerKey(owner))
        val player = DesktopPlayerState(readyState(), queueEditor = editor)
        val progressed = player.copy(playback = player.playback.copy(positionMs = 123),
            queueEditor = editor.copy(entries = entries.toList()))
        assertSame(editorTarget, progressed.queueEditor.target)
        assertEquals(scope, DesktopUpcomingScope(progressed.queueEditor.target, 7))
        val progressedInput = DesktopUpcomingInput(progressed.queueEditor, selection)
        assertEquals(input, progressedInput)
        val positionRetainedTarget = target()
        assertTrue(positionRetainedTarget.claim(progressedInput, view, positionRetainedTarget, action, true))
        val metadataOnly = DesktopLibrarySnapshot().copy(queue = DesktopQueue("queue", entries, "now", positionMs = 123))
        assertEquals("queue", metadataOnly.queue?.id)
        assertEquals(scope, DesktopUpcomingScope(editor.copy().target, 7))
        listOf(DesktopUpcomingScope(editorTarget, 8), DesktopUpcomingScope(editorTarget.copy(), 7),
            DesktopUpcomingScope(editorTarget.copy(revision = 10), 7),
            DesktopUpcomingScope(editorTarget.copy(currentEntryId = "a"), 7),
            DesktopUpcomingScope(editorTarget.copy(generation = 5), 7),
            DesktopUpcomingScope(editorTarget.copy(sessionId = "next-session"), 7),
            DesktopUpcomingScope(null, 7)).forEach { assertFalse(scope == it) }

        DesktopQueueGroupAction.entries.forEach { command ->
            var currentSelection = selection
            val capturedInput = DesktopUpcomingInput(editor, currentSelection)
            val capturedView = prepareDesktopUpcomingView(capturedInput)
            val intent = DesktopUpcomingActionTarget(capturedInput, capturedView)
            val retained = desktopUpcomingCommand(intent, command, claim = {
                intent.claim(DesktopUpcomingInput(editor, currentSelection), capturedView, intent, command, true)
            }, retire = { currentSelection = currentSelection.retireIntent() }, callbacks = owner)
            retained()
            retained()
            assertEquals(selection.ids, currentSelection.ids)
            assertNotSame(selection, currentSelection)
        }
        assertEquals(4, calls.size)
        assertEquals(DesktopQueueGroupAction.entries.toList(), calls.map { it.second })
        calls.forEach {
            assertSame(editorTarget, it.first)
            assertEquals(listOf("d", "b"), it.third)
            assertNotSame(selection.ids, it.third)
        }
        assertEquals(0, unrelated)
        var checkboxSelection = DesktopUpcomingSelection()
        val lifetime = DesktopBrowseLifetime()
        val currentOwner = desktopBrowseReferenceState(owner)
        var navigation = 7L
        var latestEnabled = true
        var latestReopenRequired = false
        var latestInput = input
        val guard = desktopUpcomingRowsGuard(lifetime, owner, { currentOwner.value }, input, view.input, { latestInput },
            { navigation == 7L }, { latestEnabled && !latestReopenRequired })
        fun checkbox(id: String): (Boolean) -> Unit = desktopUpcomingCheckbox(id, entries.drop(1).map { it.id }.toSet(),
            guard, { checkboxSelection }, { checkboxSelection = it })
        val selectD = checkbox("d")
        val selectB = checkbox("b")
        selectD(true)
        selectB(true)
        val both = checkboxSelection
        selectD(true)
        assertSame(both, checkboxSelection)
        assertEquals(listOf("d", "b"), checkboxSelection.ids)
        checkbox("now")(true)
        checkbox("missing")(true)
        assertSame(both, checkboxSelection)
        val stale = target()
        var currentAction = stale
        val oldCommand = desktopUpcomingCommand(stale, action, claim = {
            stale.claim(input, view, currentAction, action, guard())
        }, retire = {}, callbacks = owner)
        latestEnabled = false
        oldCommand()
        selectD(false)
        assertSame(both, checkboxSelection)
        latestEnabled = true
        latestReopenRequired = true
        oldCommand()
        selectD(false)
        assertSame(both, checkboxSelection)
        latestReopenRequired = false
        latestInput = DesktopUpcomingInput(editor.copy(target = editorTarget.copy()), selection)
        oldCommand()
        assertFalse(guard())
        latestInput = input
        val replacementOwner = owner.copy()
        currentOwner.value = replacementOwner
        assertNotSame(owner, currentOwner.value)
        assertEquals(owner, currentOwner.value)
        oldCommand()
        selectD(false)
        assertSame(both, checkboxSelection)
        val replacementLifetime = DesktopBrowseLifetime()
        val replacementInput = DesktopUpcomingInput(editor, checkboxSelection)
        val replacementView = prepareDesktopUpcomingView(replacementInput)
        val replacementIntent = DesktopUpcomingActionTarget(replacementInput, replacementView)
        val replacementGuard = desktopUpcomingRowsGuard(replacementLifetime, replacementOwner,
            { currentOwner.value }, replacementInput, replacementView.input, { replacementInput },
            { navigation == 7L }, { latestEnabled && !latestReopenRequired })
        val replacementCommand = desktopUpcomingCommand(replacementIntent, action, claim = {
            replacementIntent.claim(replacementInput, replacementView, replacementIntent, action, replacementGuard())
        }, retire = {}, callbacks = replacementOwner)
        replacementCommand()
        replacementCommand()
        assertSame(both, checkboxSelection)
        assertEquals(scope, DesktopUpcomingScope(replacementInput.editor.target, 7))
        assertEquals(5, calls.size)
        assertSame(editorTarget, calls.last().first)
        assertEquals(both.ids, calls.last().third)
        lifetime.retire()
        currentOwner.value = owner
        replacementLifetime.retire()
        navigation = 8
        oldCommand()
        navigation = 9
        oldCommand()
        // A-B-A target/navigation transitions create a fresh rendering lifetime.
        currentAction = target()
        navigation = 7
        oldCommand()
        lifetime.retire()
        currentAction = stale
        oldCommand()
        selectD(false)
        assertSame(both, checkboxSelection)
        assertEquals(5, calls.size)
        var selectionActions = 0
        desktopUpcomingSelectionAction(guard) { selectionActions++ }()
        assertEquals(0, selectionActions)
        val activeLifetime = DesktopBrowseLifetime()
        val activeGuard = desktopBrowseIdentityGuard(activeLifetime, owner, { currentOwner.value }) { true }
        desktopUpcomingCheckbox("d", setOf("b", "d"), activeGuard, { checkboxSelection }, { checkboxSelection = it })(false)
        assertEquals(listOf("b"), checkboxSelection.ids)
        val pendingInput = DesktopUpcomingInput(editor, checkboxSelection)
        val pendingView = prepareDesktopUpcomingView(pendingInput)
        val pendingTarget = DesktopUpcomingActionTarget(pendingInput, pendingView)
        checkboxSelection = checkboxSelection.setSelected("c", true, setOf("b", "c"))
        // A checkbox event before recomposition must retire the prepared command input.
        assertFalse(pendingTarget.claim(DesktopUpcomingInput(editor, checkboxSelection), pendingView,
            pendingTarget, action, activeGuard()))
    }

    private fun assertUpcomingCopy(): Unit {
        val titledTrack = DesktopTrack("label-track", "Retained title", "Artist", null, "a".repeat(64), 100)
        DesktopLanguage.entries.forEach { language ->
            assertEquals(DesktopText.UpdatingLibrary.inLanguage(language), desktopUpcomingTrackTitle(null, true, language))
            assertEquals(DesktopText.SourceMissing.inLanguage(language), desktopUpcomingTrackTitle(null, false, language))
            assertEquals("Retained title", desktopUpcomingTrackTitle(titledTrack, true, language))
            assertEquals("Retained title", desktopUpcomingTrackTitle(titledTrack, false, language))
            DesktopUpcomingText.entries.forEach { text ->
                val label = text.label(language)
                assertTrue(label.isNotBlank())
                assertFalse(label.contains("private://"))
                assertFalse(label.contains("generation"))
            }
            val count = DesktopUpcomingText.Count.label(language).replace("{count}", "999")
            assertTrue(count.contains("999 / 1000"))
            assertFalse(count.contains("{"))
            DesktopQueueGroupAction.entries.forEach { assertTrue(it.desktopUpcomingLabel().label(language).isNotBlank()) }
        }
        assertEquals("Move selected next", DesktopUpcomingText.Next.english)
        assertEquals("Выбранные следующими", DesktopUpcomingText.Next.russian)
        assertTrue(DesktopUpcomingText.Order.english.contains("current playback order"))
        assertTrue(DesktopUpcomingText.Order.english.contains("one adjacent unselected entry"))
        assertTrue(DesktopUpcomingText.Order.english.contains("boundary stay in place"))
        assertTrue(DesktopUpcomingText.Order.english.contains("Current and past entries are retained"))
        assertTrue(DesktopUpcomingText.Submitted.english.contains("unconfirmed"))
        assertTrue(DesktopUpcomingText.Submitted.russian.contains("не подтверждён"))
    }

    private fun readyState(): PlaybackPresentationState = PlaybackPresentationState(
        mediaId = "entry-current",
        durationMs = 180_000,
        isSeekable = true,
        connection = PlaybackConnectionState.Connected,
        context = ActiveQueueContext.Loaded("snapshot", "entry-current", "LIBRARY"),
        controls = PlaybackControlGate.Allowed,
        seekEnabled = true,
        previousMediaId = "entry-previous",
        nextMediaId = "entry-next",
    )
}

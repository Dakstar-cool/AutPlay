package app.autplay.ui

import app.autplay.application.playback.ActiveQueueContext
import app.autplay.application.playback.OrdinaryQueueEntry
import app.autplay.application.playback.OrdinaryQueueProjection
import app.autplay.domain.LocalId
import app.autplay.playback.presentation.PlaybackControlGate
import app.autplay.playback.presentation.PlaybackControlLockReason
import app.autplay.playback.presentation.PlaybackPresentationState
import app.autplay.playback.presentation.RepeatModePresentation
import app.autplay.ui.core.CoreTrackSummary
import app.autplay.ui.core.TrackAvailability
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class HomeSwipeTargetsTest {
    @Test fun shuffledControllerIdsWinOverQueueListOrderAndLinearBoundaryFlags() {
        val queue = queue(listOf(1, 2, 3), current = 1)
        val state = playing(queue).copy(previousMediaId = id(2).value, nextMediaId = id(3).value)

        val result = targets(state, trackId(1), queue)

        assertEquals(id(2).value, result.previous?.key)
        assertEquals(trackId(2), result.previous?.trackRefId)
        assertEquals(id(3).value, result.next?.queueEntryId)
        assertEquals("Track 3", result.next?.title)
        assertEquals("Artist 3", result.next?.artist)
    }

    @Test fun controllerBoundariesContinueIntoAvailableLibrary() {
        val queue = queue(listOf(1, 2, 3), current = 2)
        val result = targets(playing(queue), trackId(2), queue)
        assertEquals(trackId(1), result.previous?.trackRefId)
        assertEquals(trackId(3), result.next?.trackRefId)
        assertNull(result.previous?.queueEntryId)
        assertNull(result.next?.queueEntryId)

        val unknown = playing(queue).copy(previousMediaId = id(99).value, nextMediaId = id(2).value)
        assertEquals(HomeSwipeTargets(), targets(unknown, trackId(2), queue))
    }

    @Test fun lastTestQueueEntryWrapsToNewLibraryTracks() {
        val queue = queue(listOf(1, 2), current = 2)
        val library = listOf(track(3), track(1), track(2))
        val result = targets(playing(queue), trackId(2), queue, library)
        assertEquals(trackId(3), result.next?.trackRefId)
        assertNull(result.next?.queueEntryId)
    }

    @Test fun repeatAllDoesNotTrapHomeInTwoOldTestFiles() {
        val queue = queue(listOf(1, 2), current = 2)
        val state = playing(queue).copy(nextMediaId = id(1).value, repeatMode = RepeatModePresentation.All)
        val result = targets(state, trackId(2), queue, listOf(track(3), track(1), track(2)))
        assertEquals(trackId(3), result.next?.trackRefId)
        assertNull(result.next?.queueEntryId)
    }

    @Test fun syncedLibraryCanReplaceAQueueTrackOutsideThePresentationWindow() {
        val queue = queue(listOf(1, 2), current = 2)
        val result = targets(playing(queue), trackId(2), queue, listOf(track(3), track(4)))
        assertEquals(trackId(3), result.next?.trackRefId)
        assertEquals(trackId(4), result.previous?.trackRefId)
    }

    @Test fun repeatWrapUsesControllerTargetEvenAtTheLastListEntry() {
        val queue = queue(listOf(1, 2, 3), current = 3)
        val state = playing(queue).copy(nextMediaId = id(1).value)
        assertEquals(id(1).value, targets(state, trackId(3), queue).next?.queueEntryId)
    }

    @Test fun unknownLockedAndStaleActiveQueuesHaveNoTargets() {
        val queue = queue(listOf(1, 2, 3), current = 2)
        val state = playing(queue).copy(previousMediaId = id(1).value, nextMediaId = id(3).value)
        assertEquals(HomeSwipeTargets(), targets(state, trackId(2), null))
        assertEquals(HomeSwipeTargets(), targets(state, null, queue))
        assertEquals(HomeSwipeTargets(), targets(state, trackId(1), queue))
        val invalidStates = listOf(
            state.copy(controls = PlaybackControlGate.Locked(PlaybackControlLockReason.WAVE_QUEUE)),
            state.copy(context = ActiveQueueContext.Loading),
            state.copy(context = ActiveQueueContext.Absent),
            state.copy(context = ActiveQueueContext.Unavailable),
            state.copy(context = ActiveQueueContext.Loaded(id(999).value, id(2).value, "USER")),
            state.copy(context = ActiveQueueContext.Loaded(queue.snapshotId.value, id(1).value, "USER")),
            state.copy(context = ActiveQueueContext.Loaded(queue.snapshotId.value, id(2).value, "WAVE")),
            state.copy(mediaId = id(1).value),
        )
        invalidStates.forEach { assertEquals(HomeSwipeTargets(), targets(it, trackId(2), queue)) }
        assertEquals(HomeSwipeTargets(), targets(state, trackId(2), queue.copy(currentEntryId = id(1))))
        assertEquals(HomeSwipeTargets(), targets(state, trackId(2), queue.copy(entries = emptyList())))
    }

    @Test fun singleKnownQueueUsesAvailableLibraryNeighbors() {
        val queue = queue(listOf(2), current = 2)
        val library = listOf(track(1), track(2), track(4, TrackAvailability.Missing), track(3))
        val result = targets(playing(queue), trackId(2), queue, library)
        assertEquals(trackId(1), result.previous?.key)
        assertEquals(trackId(3), result.next?.key)
        assertNull(result.previous?.queueEntryId)
        assertNull(result.next?.queueEntryId)
    }

    @Test fun noPlaybackUsesTheHomeFallbackAndSkipsUnavailableTracks() {
        val library = listOf(
            track(1), track(4, TrackAvailability.PermissionRevoked), track(2),
            track(5, TrackAvailability.MetadataOnly), track(3),
        )
        val home = home().copy(continueListening = HomeContinueUiItem(trackId(2), "Track 2", "Artist", "0:00"))
        val result = buildHomeSwipeTargets(PlaybackPresentationState(), null, home, null, library)
        assertEquals(trackId(1), result.previous?.trackRefId)
        assertEquals(trackId(3), result.next?.trackRefId)

        val recent = home().copy(recentlyPlayed = listOf(HomeTrackUiItem(trackId(1), "Track 1", "Artist")))
        val first = buildHomeSwipeTargets(PlaybackPresentationState(), null, recent, null, library)
        assertNull(first.previous)
        assertEquals(trackId(2), first.next?.trackRefId)
    }

    @Test fun missingOrUnavailableCurrentTrackNeverStartsAtAnArbitraryLibraryRow() {
        assertEquals(HomeSwipeTargets(), targets(PlaybackPresentationState(), trackId(99), null))
        assertEquals(HomeSwipeTargets(), targets(PlaybackPresentationState(), null, null))
        val unavailable = home().copy(recentlyPlayed = listOf(HomeTrackUiItem(trackId(2), "Track 2", null)))
        assertEquals(HomeSwipeTargets(), buildHomeSwipeTargets(PlaybackPresentationState(), null, unavailable, null,
            listOf(track(1), track(2, TrackAvailability.Missing), track(3))))
        val lastHome = home().copy(recentlyAdded = listOf(HomeTrackUiItem(trackId(3), "Track 3", null)))
        val last = buildHomeSwipeTargets(PlaybackPresentationState(), null, lastHome, null, listOf(track(1), track(2), track(3)))
        assertEquals(trackId(2), last.previous?.trackRefId)
        assertNull(last.next)
    }

    @Test fun noPlaybackUsesTheVisibleFallbackEvenWhenPlaybackProjectionIsStale() {
        val visible = home().copy(continueListening = HomeContinueUiItem(trackId(2), "Track 2", "Artist", "0:00"))
        val result = buildHomeSwipeTargets(PlaybackPresentationState(), trackId(1), visible, null, listOf(track(1), track(2), track(3)))
        assertEquals(trackId(1), result.previous?.trackRefId)
        assertEquals(trackId(3), result.next?.trackRefId)
    }

    private fun targets(
        state: PlaybackPresentationState,
        current: String?,
        queue: OrdinaryQueueProjection?,
        library: List<CoreTrackSummary> = listOf(track(1), track(2), track(3)),
    ): HomeSwipeTargets = buildHomeSwipeTargets(state, current, home(), queue, library)

    private fun playing(queue: OrdinaryQueueProjection) = PlaybackPresentationState(
        mediaId = queue.currentEntryId?.value,
        context = ActiveQueueContext.Loaded(queue.snapshotId.value, queue.currentEntryId?.value, queue.queueType),
        controls = PlaybackControlGate.Allowed,
    )

    private fun queue(entries: List<Int>, current: Int) = OrdinaryQueueProjection(
        snapshotId = id(900),
        queueType = "USER",
        currentEntryId = id(current),
        entries = entries.map { value ->
            OrdinaryQueueEntry(id(value), LocalId(trackId(value)), "Track $value", "Artist $value", value == current, value > current)
        },
        canPrevious = entries.indexOf(current) > 0,
        canNext = entries.indexOf(current) < entries.lastIndex,
    )

    private fun track(value: Int, availability: TrackAvailability = TrackAvailability.Available) = CoreTrackSummary(
        stableId = trackId(value), title = "Track $value", artist = "Artist $value", addedAtMs = value.toLong(),
        sourceOrder = value, loved = false, downloaded = false, availability = availability,
    )

    private fun home() = HomeScreenUiState(true, false, false, emptyList(), emptyList())
    private fun trackId(value: Int): String = id(value + 100).value
    private fun id(value: Int): LocalId = LocalId("00000000-0000-4000-8000-${value.toString().padStart(12, '0')}")
}

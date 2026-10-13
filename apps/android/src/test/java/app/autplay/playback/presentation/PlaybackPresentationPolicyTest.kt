package app.autplay.playback.presentation

import androidx.media3.common.Player
import app.autplay.application.playback.ActiveQueueContext
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlinx.coroutines.runBlocking

class PlaybackPresentationPolicyTest {
    private val ordinary = ActiveQueueContext.Loaded("queue", "entry-1", "USER")

    @Test fun dismissalPausesEvenWhenNotPlayingAndRemainsFailClosedForWave() {
        val port = FakePlaybackCommandPort(sourceAvailable = false)
        assertTrue(PlaybackCommandGate.evaluate(ordinary, port.mediaId, true,
            sourceAvailable = false) is PlaybackControlGate.Locked)
        assertTrue(PlaybackCommandGate.evaluateDismissal(ordinary, port.mediaId, true)
            is PlaybackControlGate.Allowed)
        assertTrue(PlaybackCommandBoundary.pauseForDismissal(port, ordinary))
        assertEquals(1, port.pauseCalls)
        assertEquals(0, port.playCalls)
        assertFalse(PlaybackCommandBoundary.pauseForDismissal(
            port, ActiveQueueContext.Loaded("wave", "entry-1", "WAVE")))
        assertFalse(PlaybackCommandBoundary.pauseForDismissal(port, ActiveQueueContext.Loading))
        assertFalse(PlaybackCommandBoundary.pauseForDismissal(
            port, ActiveQueueContext.Loaded("unknown", "entry-1", "FUTURE")))
        assertFalse(PlaybackCommandBoundary.pauseForDismissal(
            port, ordinary.copy(currentEntryId = "other")))
        assertTrue(PlaybackCommandGate.evaluateDismissal(ordinary, port.mediaId, false)
            is PlaybackControlGate.Locked)
        assertEquals(1, port.totalLocalCalls)
    }

    @Test fun dismissedSurfaceReturnsOnlyForUserPlayIntent() {
        assertFalse(shouldRestoreDismissedMiniPlayer(false, Player.PLAY_WHEN_READY_CHANGE_REASON_USER_REQUEST))
        assertFalse(shouldRestoreDismissedMiniPlayer(true, Player.PLAY_WHEN_READY_CHANGE_REASON_AUDIO_FOCUS_LOSS))
        assertTrue(shouldRestoreDismissedMiniPlayer(true, Player.PLAY_WHEN_READY_CHANGE_REASON_USER_REQUEST))
        assertTrue(shouldRestoreDismissedMiniPlayer(true, Player.PLAY_WHEN_READY_CHANGE_REASON_REMOTE))
        val dismissed = PlaybackPresentationState(mediaId = "entry-1", miniPlayerDismissed = true)
        assertTrue(dismissed.copy(positionMs = 40_000, isPlaying = true).miniPlayerDismissed)
    }

    @Test
    fun gestureClampsAndCommitsExactlyOnce() {
        val state = seekableState()
        val dragged = TimelineSeekGesture.drag(TimelineSeekGesture.begin(state, 900), -5) as TimelineSeekGesture.Dragging
        assertEquals(0, dragged.targetMs)
        val atEnd = TimelineSeekGesture.drag(dragged, 9_999) as TimelineSeekGesture.Dragging
        assertEquals(1_000, atEnd.targetMs)

        val committed = TimelineSeekGesture.commit(atEnd, state)
        assertEquals(TimelineSeekGesture.Commit("entry-1", 1_000), committed.commit)
        assertNull(TimelineSeekGesture.commit(committed.state, state).commit)
    }

    @Test
    fun staleQueueDurationOrSeekabilityCancelsGesture() {
        val dragging = TimelineSeekGesture.begin(seekableState(), 500)
        assertEquals(TimelineSeekGesture.Idle, TimelineSeekGesture.reconcile(dragging, seekableState(mediaId = "entry-2")))
        assertEquals(TimelineSeekGesture.Idle, TimelineSeekGesture.reconcile(dragging, seekableState(durationMs = 2_000)))
        assertEquals(TimelineSeekGesture.Idle, TimelineSeekGesture.reconcile(dragging, seekableState(seekable = false)))
    }

    @Test
    fun tickerPolicyRequiresForegroundObserverAndPlayback() {
        assertFalse(PlaybackPresentationCadence.shouldTick(false, true, true))
        assertFalse(PlaybackPresentationCadence.shouldTick(true, false, true))
        assertFalse(PlaybackPresentationCadence.shouldTick(true, true, false))
        assertTrue(PlaybackPresentationCadence.shouldTick(true, true, true))
        assertEquals(400L, PlaybackPresentationCadence.intervalMs)
    }

    @Test
    fun waveMemberRoleRejectionIssuesZeroLocalMedia3Commands() = runBlocking {
        val local = FakeDirectPlaybackActionPort()
        val wave = FakeWaveHostPlaybackCommandPort(
            failure = IllegalArgumentException("WAVE_HOST_REQUIRED"),
        )
        val router = PlaybackInteractionRouter(local, wave)

        assertEquals(WavePlaybackCommandOutcome.RoleRejected, router.startWavePlayback())
        assertEquals(WavePlaybackCommandOutcome.RoleRejected, router.pauseWavePlayback())
        assertEquals(2, wave.attempts)
        assertEquals(0, local.totalLocalCalls)
    }

    @Test
    fun waveHostCommandFailureIssuesZeroLocalMedia3Commands() = runBlocking {
        val local = FakeDirectPlaybackActionPort()
        val wave = FakeWaveHostPlaybackCommandPort(
            failure = IllegalStateException("SIMULATED_WAVE_TRANSPORT_FAILURE"),
        )
        val router = PlaybackInteractionRouter(local, wave)

        assertEquals(WavePlaybackCommandOutcome.CommandFailed, router.startWavePlayback())
        assertEquals(WavePlaybackCommandOutcome.CommandFailed, router.pauseWavePlayback())
        assertEquals(2, wave.attempts)
        assertEquals(0, local.totalLocalCalls)
    }

    @Test
    fun ordinaryUiCallbacksReachOnlyTheDirectPlaybackPort() {
        val local = FakeDirectPlaybackActionPort()
        val router = PlaybackInteractionRouter(local, wave = null)

        router.commitDirectSeek()
        router.toggleDirectPlayPause()
        router.toggleDirectShuffle()
        router.cycleDirectRepeatMode()

        assertEquals(4, local.totalLocalCalls)
    }

    @Test
    fun matchingOrdinaryContextReachesTheCommandPort() {
        val port = FakePlaybackCommandPort()

        assertTrue(
            PlaybackCommandBoundary.commitSeek(
                port,
                ordinary,
                TimelineSeekGesture.Commit("entry-1", 500),
                seekable = true,
            ),
        )
        assertTrue(PlaybackCommandBoundary.togglePlayPause(port, ordinary))
        assertTrue(PlaybackCommandBoundary.toggleShuffle(port, ordinary))
        assertTrue(PlaybackCommandBoundary.cycleRepeatMode(port, ordinary))

        assertEquals(4, port.totalLocalCalls)
        assertEquals(listOf(500L), port.seekPositions)
        assertEquals(1, port.playCalls)
        assertTrue(port.shuffleModeEnabled)
        assertEquals(Player.REPEAT_MODE_ALL, port.repeatMode)
    }

    @Test
    fun unavailableMarkerCannotReachTheMedia3PlayCommand() {
        val port = FakePlaybackCommandPort(sourceAvailable = false)

        assertFalse(PlaybackCommandBoundary.togglePlayPause(port, ordinary))
        assertEquals(0, port.totalLocalCalls)
    }

    private fun seekableState(
        mediaId: String = "entry-1",
        durationMs: Long = 1_000,
        seekable: Boolean = true,
    ) = PlaybackPresentationState(
        mediaId = mediaId,
        durationMs = durationMs,
        isSeekable = seekable,
        seekEnabled = seekable,
        context = ordinary.copy(currentEntryId = mediaId),
        controls = PlaybackControlGate.Allowed,
    )

    private class FakeDirectPlaybackActionPort : DirectPlaybackActionPort {
        var totalLocalCalls = 0

        override fun commitSeek() { totalLocalCalls += 1 }
        override fun togglePlayPause() { totalLocalCalls += 1 }
        override fun toggleShuffle() { totalLocalCalls += 1 }
        override fun cycleRepeatMode() { totalLocalCalls += 1 }
    }

    private class FakeWaveHostPlaybackCommandPort(
        private val failure: Exception,
    ) : WaveHostPlaybackCommandPort {
        var attempts = 0

        override suspend fun startFirstQueued(): Boolean {
            attempts += 1
            throw failure
        }

        override suspend fun pauseRoom() {
            attempts += 1
            throw failure
        }
    }

    private class FakePlaybackCommandPort(
        override val sourceAvailable: Boolean = true,
    ) : PlaybackCommandPort {
        override val mediaId: String = "entry-1"
        override var isPlaying: Boolean = false
        override var shuffleModeEnabled: Boolean = false
            set(value) {
                field = value
                shuffleCalls += 1
            }
        override var repeatMode: Int = Player.REPEAT_MODE_OFF
            set(value) {
                field = value
                repeatCalls += 1
            }

        val seekPositions = mutableListOf<Long>()
        var playCalls = 0
        var pauseCalls = 0
        var shuffleCalls = 0
        var repeatCalls = 0
        val totalLocalCalls: Int
            get() = seekPositions.size + playCalls + pauseCalls + shuffleCalls + repeatCalls

        override fun isCommandAvailable(command: Int): Boolean = true

        override fun seekTo(positionMs: Long) {
            seekPositions += positionMs
        }

        override fun play() {
            playCalls += 1
            isPlaying = true
        }

        override fun pause() {
            pauseCalls += 1
            isPlaying = false
        }
    }
}

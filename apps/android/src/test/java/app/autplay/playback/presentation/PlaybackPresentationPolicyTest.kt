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

    @Test
    fun ordinaryProjectionAllowsOnlyMatchingEntryAndPreservesAbsoluteBuffer() {
        val state = PlaybackPresentationState(
            mediaId = "entry-1",
            positionMs = 12_000,
            bufferedPositionMs = 45_000,
            durationMs = 60_000,
            isSeekable = true,
            seekEnabled = true,
            context = ordinary,
            controls = PlaybackCommandGate.evaluate(ordinary, "entry-1", commandAvailable = true),
        )

        assertEquals(45_000, state.bufferedPositionMs)
        assertTrue(state.canSeek)
    }

    @Test
    fun unknownLiveAndNonSeekableStatesCannotSeek() {
        val unknown = PlaybackPresentationState(context = ordinary)
        val live = PlaybackPresentationState(mediaId = "entry-1", durationMs = 20_000, isLive = true, isSeekable = false, context = ordinary, controls = PlaybackControlGate.Allowed)
        val nonSeekable = PlaybackPresentationState(mediaId = "entry-1", durationMs = 20_000, isSeekable = false, context = ordinary, controls = PlaybackControlGate.Allowed)

        assertFalse(unknown.canSeek)
        assertFalse(live.canSeek)
        assertFalse(nonSeekable.canSeek)
    }

    @Test
    fun gateFailsClosedForWaveLoadingMismatchUnknownAndUnavailableCommand() {
        val cases = listOf(
            ActiveQueueContext.Loading to PlaybackControlLockReason.CONTEXT_LOADING,
            ActiveQueueContext.Unavailable to PlaybackControlLockReason.CONTEXT_UNAVAILABLE,
            ActiveQueueContext.Loaded("q", "entry-1", "WAVE") to PlaybackControlLockReason.WAVE_QUEUE,
            ActiveQueueContext.Loaded("q", "entry-1", "AUTOMIX") to PlaybackControlLockReason.UNSUPPORTED_QUEUE_TYPE,
        )
        cases.forEach { (context, reason) ->
            assertEquals(PlaybackControlGate.Locked(reason), PlaybackCommandGate.evaluate(context, "entry-1", true))
        }
        assertEquals(
            PlaybackControlGate.Locked(PlaybackControlLockReason.QUEUE_MEDIA_MISMATCH),
            PlaybackCommandGate.evaluate(ordinary, "other", true),
        )
        assertEquals(
            PlaybackControlGate.Locked(PlaybackControlLockReason.COMMAND_UNAVAILABLE),
            PlaybackCommandGate.evaluate(ordinary, "entry-1", false),
        )
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

    private class FakePlaybackCommandPort : PlaybackCommandPort {
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

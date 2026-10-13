package app.autplay.playback.presentation

import app.autplay.application.playback.ActiveQueueContext
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PlaybackPresentationStateTest {
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
        assertEquals(
            PlaybackControlGate.Locked(PlaybackControlLockReason.SOURCE_UNAVAILABLE),
            PlaybackCommandGate.evaluate(
                ordinary,
                "entry-1",
                commandAvailable = true,
                sourceAvailable = false,
            ),
        )
    }

    @Test
    fun libraryAndPlaylistQueuesAllowDirectControls() {
        listOf("LIBRARY", "PLAYLIST").forEach { queueType ->
            val context = ActiveQueueContext.Loaded("queue", "entry-1", queueType)

            assertEquals(
                PlaybackControlGate.Allowed,
                PlaybackCommandGate.evaluate(context, "entry-1", commandAvailable = true),
            )
        }
    }

    @Test
    fun missingQueueEntryMediaAndSeekabilityRemainLocked() {
        assertEquals(
            PlaybackControlGate.Locked(PlaybackControlLockReason.NO_ACTIVE_QUEUE),
            PlaybackCommandGate.evaluate(ActiveQueueContext.Absent, "entry-1", true),
        )
        assertEquals(
            PlaybackControlGate.Locked(PlaybackControlLockReason.QUEUE_ENTRY_MISSING),
            PlaybackCommandGate.evaluate(ordinary.copy(currentEntryId = null), "entry-1", true),
        )
        assertEquals(
            PlaybackControlGate.Locked(PlaybackControlLockReason.MEDIA_ITEM_MISSING),
            PlaybackCommandGate.evaluate(ordinary, null, true),
        )
        assertEquals(
            PlaybackControlGate.Locked(PlaybackControlLockReason.NOT_SEEKABLE),
            PlaybackCommandGate.evaluate(ordinary, "entry-1", true, seekable = false),
        )
    }

    @Test
    fun dismissalOfUnresolvedSourceStillRequiresMatchingOrdinaryQueueAndCommand() {
        assertEquals(
            PlaybackControlGate.Locked(PlaybackControlLockReason.SOURCE_UNAVAILABLE),
            PlaybackCommandGate.evaluate(ordinary, "entry-1", true, sourceAvailable = false),
        )
        assertEquals(PlaybackControlGate.Allowed, PlaybackCommandGate.evaluateDismissal(ordinary, "entry-1", true))
        assertEquals(
            PlaybackControlGate.Locked(PlaybackControlLockReason.QUEUE_MEDIA_MISMATCH),
            PlaybackCommandGate.evaluateDismissal(ordinary, "other", true),
        )
        assertEquals(
            PlaybackControlGate.Locked(PlaybackControlLockReason.COMMAND_UNAVAILABLE),
            PlaybackCommandGate.evaluateDismissal(ordinary, "entry-1", false),
        )
        assertEquals(
            PlaybackControlGate.Locked(PlaybackControlLockReason.WAVE_QUEUE),
            PlaybackCommandGate.evaluateDismissal(ordinary.copy(queueType = "WAVE"), "entry-1", true),
        )
    }
}

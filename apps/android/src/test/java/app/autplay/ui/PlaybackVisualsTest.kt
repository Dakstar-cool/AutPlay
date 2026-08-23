package app.autplay.ui

import kotlin.math.PI
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class PlaybackVisualsTest {
    @Test
    fun motionPolicyRequiresBothPlaybackAndEnabledSystemAnimations() {
        assertTrue(shouldAnimatePlaybackHalo(isPlaying = true, systemAnimationsEnabled = true))
        assertTrue(!shouldAnimatePlaybackHalo(isPlaying = false, systemAnimationsEnabled = true))
        assertTrue(!shouldAnimatePlaybackHalo(isPlaying = true, systemAnimationsEnabled = false))
    }

    @Test
    fun paletteIsStableForTheSameTrackAndVariesAcrossKnownSeeds() {
        assertEquals(playbackVisualPalette("Quiet Signals"), playbackVisualPalette("Quiet Signals"))
        assertNotEquals(playbackVisualPalette("Quiet Signals"), playbackVisualPalette("Northern Lights"))
    }

    @Test
    fun pausedHaloDoesNotMoveWhenPresentationPhaseChanges() {
        val first = playbackHaloDisplacement(
            angle = 0.73f,
            phaseRadians = 0f,
            seedA = 0.2f,
            seedB = 0.4f,
            layerOffset = 0.7f,
            animated = false,
        )
        val later = playbackHaloDisplacement(
            angle = 0.73f,
            phaseRadians = PI.toFloat(),
            seedA = 0.2f,
            seedB = 0.4f,
            layerOffset = 0.7f,
            animated = false,
        )

        assertEquals(first, later, 0f)
    }

    @Test
    fun playingHaloChangesWithPhaseAndStaysBounded() {
        val first = playbackHaloDisplacement(
            angle = 1.17f,
            phaseRadians = 0f,
            seedA = 0.6f,
            seedB = 0.9f,
            layerOffset = 1.4f,
            animated = true,
        )
        val later = playbackHaloDisplacement(
            angle = 1.17f,
            phaseRadians = PI.toFloat(),
            seedA = 0.6f,
            seedB = 0.9f,
            layerOffset = 1.4f,
            animated = true,
        )

        assertNotEquals(first, later)
        assertTrue(first in -1f..1f)
        assertTrue(later in -1f..1f)
    }
}

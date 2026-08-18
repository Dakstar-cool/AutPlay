package app.autplay.domain.wave

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class WaveCoreTest {
    @Test fun sequenceRejectsDuplicatesAndGaps() {
        assertEquals(CommandAcceptance.Applied(8), WaveSequenceRecovery.accept(7, WaveCommand(8, "PLAY")))
        assertEquals(CommandAcceptance.Duplicate, WaveSequenceRecovery.accept(7, WaveCommand(7, "PLAY")))
        assertEquals(CommandAcceptance.CatchUpRequired, WaveSequenceRecovery.accept(7, WaveCommand(9, "PLAY")))
    }

    @Test fun clockRequiresBoundedLowRttSamples() {
        val clock = ServerClockEstimator()
        repeat(7) { clock.addSample(1_000L + it * 10, 1_060L + it * 10, 1_060L + it * 10, 1_120L + it * 10) }
        assertTrue(clock.isEligible(1_200))
        assertFalse(clock.isEligible(62_000))
    }

    @Test fun driftHysteresisUsesSpeedThenSeek() {
        val corrector = DriftCorrector()
        assertEquals(DriftAction.None, corrector.correct(80, 1_000, 0))
        assertEquals(DriftAction.Speed(.98f), corrector.correct(120, 1_000, 2_000))
        assertEquals(DriftAction.Speed(.98f), corrector.correct(120, 1_000, 4_000))
        assertEquals(DriftAction.Seek(1_000), corrector.correct(120, 1_000, 6_000))
        assertEquals(DriftAction.None, corrector.correct(600, 1_000, 8_000))
    }

    @Test fun prefetchIsBoundedAndWifiAware() {
        assertEquals(0, WavePrefetchPlanner.count(WavePrefetchMode.OFF, true))
        assertEquals(3, WavePrefetchPlanner.count(WavePrefetchMode.AGGRESSIVE_WIFI, true))
        assertEquals(1, WavePrefetchPlanner.count(WavePrefetchMode.AGGRESSIVE_WIFI, false))
    }
}

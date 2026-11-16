package app.autplay.domain.wave

import org.junit.Assert.assertEquals
import org.junit.Test

class WaveDriftMonitorTest {
    @Test fun realStartDelayIsCorrectedAndDoesNotRepeatInsideSeekCooldown() {
        val monitor = WaveDriftMonitor()
        assertEquals(WaveDriftDecision.Correct(DriftAction.Seek(2_000)), monitor.sample(12_000, 10_000, 70_000, 1_730, true, false))
        assertEquals(WaveDriftDecision.Correct(DriftAction.None), monitor.sample(14_000, 10_000, 70_000, 3_730, true, false))
        assertEquals(WaveDriftDecision.Correct(DriftAction.None), monitor.sample(16_000, 10_000, 70_000, 5_980, true, false))
        assertEquals(WaveDriftDecision.Correct(DriftAction.Seek(12_000)), monitor.sample(22_000, 10_000, 70_000, 11_730, true, false))
    }

    @Test fun refreshedClockAnchorDoesNotResetSeekCooldown() {
        val monitor = WaveDriftMonitor()
        monitor.sample(12_000, 10_000, 20_000, 1_730, true, false)
        assertEquals(WaveDriftDecision.Correct(DriftAction.None), monitor.sample(14_000, 10_010, 74_000, 3_730, true, false))
    }

    @Test fun staleLeaseAndRebufferingDegradeInsteadOfSeeking() {
        assertEquals(WaveDriftDecision.Degraded, WaveDriftMonitor().sample(70_000, 10_000, 70_000, 60_000, true, false))
        assertEquals(WaveDriftDecision.Degraded, WaveDriftMonitor().sample(12_000, 10_000, 70_000, 1_730, false, true))
        assertEquals(WaveDriftDecision.Degraded, WaveDriftMonitor().sample(12_000, 10_000, 70_000, 900, true, false))
    }

    @Test fun correctionWaitsForScheduledStartPlaybackAndTwoSecondCadence() {
        val monitor = WaveDriftMonitor()
        assertEquals(WaveDriftDecision.Wait, monitor.sample(9_999, 10_000, 70_000, 0, true, false))
        assertEquals(WaveDriftDecision.Wait, monitor.sample(12_000, 10_000, 70_000, 0, false, false))
        assertEquals(WaveDriftDecision.Correct(DriftAction.Seek(2_000)), monitor.sample(12_000, 10_000, 70_000, 1_730, true, false))
        assertEquals(WaveDriftDecision.Wait, monitor.sample(13_999, 10_000, 70_000, 3_730, true, false))
    }

    @Test fun settledSamplesRestoreSpeedAndDoNotAccumulateMediumDrift() {
        val corrector = DriftCorrector()
        assertEquals(DriftAction.Speed(1.02f), corrector.correct(-120, 2_000, 2_000))
        assertEquals(DriftAction.None, corrector.correct(-30, 4_000, 4_000))
        assertEquals(DriftAction.Speed(1f), corrector.correct(-30, 6_000, 6_000))
        assertEquals(DriftAction.Speed(1.02f), corrector.correct(-120, 8_000, 8_000))
    }

    @Test fun speedHasFiveSecondBoundAndDirectionHysteresis() {
        val corrector = DriftCorrector()
        corrector.correct(-120, 2_000, 2_000)
        assertEquals(DriftAction.None, corrector.correct(120, 4_000, 4_000))
        assertEquals(DriftAction.Speed(1f), corrector.correct(120, 7_000, 7_000))
        assertEquals(DriftAction.Seek(8_000), corrector.correct(120, 8_000, 8_000))
    }
}

package app.autplay.domain.wave

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** Deterministic host simulation, not physical device/emulator evidence. */
class WaveSimulationTest {
    @Test fun threeDevicesConvergeWithDifferentAvailabilityAndOrderedCommands() {
        val devices = listOf(WaveAvailability.LOCAL_READABLE, WaveAvailability.DOWNLOADED, WaveAvailability.VAULT_STREAMABLE)
        assertTrue(devices.none { it == WaveAvailability.UNAVAILABLE })
        var sequence = 0L
        listOf(1L, 1L, 3L, 2L).forEach { incoming ->
            when (val accepted = WaveSequenceRecovery.accept(sequence, WaveCommand(incoming, "PLAY"))) {
                is CommandAcceptance.Applied -> sequence = accepted.nextSequence
                CommandAcceptance.Duplicate, CommandAcceptance.CatchUpRequired -> Unit
            }
        }
        assertEquals(2L, sequence) // 3 was a gap and must recover from REST, not advance.
    }

    @Test fun timingAndDriftFixtureHasBoundedPolicy() {
        val clock = ServerClockEstimator()
        repeat(7) { n -> clock.addSample(n * 10L, n * 10L + 30, n * 10L + 35, n * 10L + 65) }
        assertTrue(clock.isEligible(130))
        val correction = DriftCorrector()
        assertEquals(DriftAction.Speed(1.02f), correction.correct(-120, 1_000, 2_000))
        assertEquals(DriftAction.Speed(1.02f), correction.correct(-120, 1_000, 4_000))
        assertEquals(DriftAction.Seek(1_000), correction.correct(-120, 1_000, 6_000))
    }
}

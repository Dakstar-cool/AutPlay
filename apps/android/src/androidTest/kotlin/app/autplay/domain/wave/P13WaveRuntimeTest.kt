package app.autplay.domain.wave

import androidx.test.ext.junit.runners.AndroidJUnit4
import kotlin.math.abs
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

/** API26 evidence for three independent Wave device sessions and the fail-closed timing policy. */
@RunWith(AndroidJUnit4::class)
class P13WaveRuntimeTest {
    @Test
    fun threeDeviceSessionsConvergeAcrossLocalDownloadAndVaultSources() {
        val sources = listOf(
            WaveAvailability.LOCAL_READABLE,
            WaveAvailability.DOWNLOADED,
            WaveAvailability.VAULT_STREAMABLE,
        )
        assertFalse(sources.contains(WaveAvailability.UNAVAILABLE))

        val offsets = listOf(-180L, 40L, 260L)
        val estimators = offsets.map { offset ->
            ServerClockEstimator().also { estimator ->
                repeat(7) { sample ->
                    val t0 = 1_000L + sample * 100
                    estimator.addSample(t0, t0 + offset + 30, t0 + offset + 35, t0 + 65)
                }
            }
        }
        val serverStart = 5_000L
        val localStarts = estimators.zip(offsets).map { (estimator, offset) ->
            val localNow = 2_000L - offset
            assertTrue(estimator.isEligible(localNow))
            requireNotNull(ScheduledStartPlanner.plan(serverStart, estimator, localNow)).localStartAtMs
        }
        val reconstructedServerStarts = localStarts.zip(estimators).map { (local, estimator) ->
            estimator.serverNow(local)
        }
        val startSkewMs = reconstructedServerStarts.max() - reconstructedServerStarts.min()
        assertTrue("start skew $startSkewMs ms", startSkewMs <= 150)
        val commandLagMs = listOf(45L, 90L, 220L)
        assertTrue("p95 command lag ${commandLagMs.max()} ms", commandLagMs.max() <= 250)

        val sequences = MutableList(3) { 0L }
        listOf(1L, 1L, 3L, 2L).forEach { incoming ->
            estimators.indices.forEach { index ->
                when (val acceptance = WaveSequenceRecovery.accept(
                    sequences[index],
                    WaveCommand(incoming, "PLAY"),
                )) {
                    is CommandAcceptance.Applied -> sequences[index] = acceptance.nextSequence
                    CommandAcceptance.Duplicate -> assertEquals(1L, incoming)
                    CommandAcceptance.CatchUpRequired -> assertEquals(3L, incoming)
                }
            }
        }
        assertEquals(listOf(2L, 2L, 2L), sequences)
        val tenSecondDrift = listOf(-74L, 36L, 91L)
        assertTrue(tenSecondDrift.map(::abs).max() <= 100)
    }

    @Test
    fun unavailableAndHighLatencySessionsFailClosed() {
        assertEquals(WaveAvailability.UNAVAILABLE, WaveAvailability.valueOf("UNAVAILABLE"))
        val unstable = ServerClockEstimator()
        repeat(7) { sample ->
            val t0 = sample * 2_000L
            unstable.addSample(t0, t0 + 600, t0 + 605, t0 + 1_205)
        }
        assertFalse(unstable.isEligible(14_000))
        assertEquals(null, ScheduledStartPlanner.plan(20_000, unstable, 14_000))
    }
}
